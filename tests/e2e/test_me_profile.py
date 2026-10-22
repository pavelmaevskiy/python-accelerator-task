import pytest
from sqlalchemy import select

from app.models.identity import OneTimeToken, Profile, User
from tests.conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration

ME = "/api/v1/me"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
GIF = b"GIF89a" + b"0" * 64


async def test_get_me_returns_profile_with_resolved_trainer_id(
    player_client, make_trainer, link_player
) -> None:
    trainer = await make_trainer()
    await link_player(trainer, player_client.profile)
    plain = await player_client.get(ME)
    ctx = await player_client.get(ME, headers={"X-Trainer-Context": str(trainer.id)})
    assert plain.status_code == 200 and plain.json()["trainer_id"] is None
    body = ctx.json()
    assert body["trainer_id"] == str(trainer.id)
    assert body["role"] == "player" and body["is_child"] is False
    assert body["trainer_profile"] is None and body["coach_profile"] is None


async def test_get_me_includes_trainer_and_coach_profiles(trainer_client, coach_client) -> None:
    t = (await trainer_client.get(ME)).json()
    assert t["trainer_profile"]["business_name"] == "Acme Tennis" and t["trainer_id"] == t["id"]
    c = (await coach_client.get(ME)).json()
    assert c["coach_profile"]["is_public"] is False and c["trainer_id"] is not None


async def test_patch_me_updates_allowed_fields_only(player_client, db_session) -> None:
    r = await player_client.patch(
        ME,
        json={
            "first_name": "Ann",
            "last_name": "Lee",
            "phone": "+15551234567",
            "school": "PS 1",
            "timezone": "Europe/Warsaw",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["first_name"], body["school"], body["timezone"]) == (
        "Ann",
        "PS 1",
        "Europe/Warsaw",
    )
    profile = await db_session.get(Profile, player_client.user.id, populate_existing=True)
    assert profile.phone == "+15551234567"


async def test_patch_me_is_partial_and_null_clears_nullable_fields(player_client) -> None:
    await player_client.patch(ME, json={"school": "PS 1", "phone": "+15551234567"})
    r = await player_client.patch(ME, json={"school": None})
    assert r.json()["school"] is None and r.json()["phone"] == "+15551234567"
    assert r.json()["first_name"] == "Test"


@pytest.mark.parametrize("field", ["role", "email", "status", "is_email_verified"])
async def test_patch_me_rejects_forbidden_fields_with_422(player_client, field) -> None:
    r = await player_client.patch(ME, json={field: "x"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [{"timezone": "Mars/Base"}, {"phone": "555"}, {"first_name": ""}, {"first_name": "x" * 101}],
)
async def test_patch_me_validates_values(player_client, payload) -> None:
    assert (await player_client.patch(ME, json=payload)).status_code == 422


async def test_presign_returns_quarantine_key_and_300s_expiry(player_client, fake_storage) -> None:
    r = await player_client.put(
        f"{ME}/avatar", json={"content_type": "image/png", "content_length": 1000}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["key"].startswith(f"quarantine/{player_client.user.id}/")
    assert body["expires_in"] == 300 and body["upload_url"].startswith("https://storage.test/")
    assert fake_storage.presigned == [(body["key"], "image/png", 1000, 300)]


@pytest.mark.parametrize(
    "payload",
    [
        {"content_type": "image/gif", "content_length": 10},
        {"content_type": "image/svg+xml", "content_length": 10},
        {"content_type": "image/png", "content_length": 5 * 1024 * 1024 + 1},
        {"content_type": "image/png", "content_length": 0},
    ],
)
async def test_presign_rejects_bad_content_type_and_over_5mb(player_client, payload) -> None:
    assert (await player_client.put(f"{ME}/avatar", json=payload)).status_code == 422


async def _presigned_key(client) -> str:
    r = await client.put(f"{ME}/avatar", json={"content_type": "image/png", "content_length": 100})
    return r.json()["key"]


async def test_patch_avatar_key_confirms_checks_magic_bytes_copies_and_deletes_quarantine(
    player_client, fake_storage, db_session
) -> None:
    key = await _presigned_key(player_client)
    fake_storage.seed(key, PNG, "image/png")
    r = await player_client.patch(ME, json={"avatar_key": key})
    assert r.status_code == 200
    url = r.json()["avatar_url"]
    assert url.startswith("https://cdn.test/avatars/") and url.endswith(".png")
    assert "quarantine" not in url
    assert fake_storage.keys("quarantine/") == []
    [final] = fake_storage.keys("avatars/")
    assert fake_storage.objects[final] == PNG
    profile = await db_session.get(Profile, player_client.user.id, populate_existing=True)
    assert profile.avatar_key == final


async def test_replacing_avatar_enqueues_deletion_of_the_old_one(
    player_client, fake_storage, fake_queue
) -> None:
    for _ in range(2):
        key = await _presigned_key(player_client)
        fake_storage.seed(key, PNG, "image/png")
        await player_client.patch(ME, json={"avatar_key": key})
    [job] = fake_queue.named("delete_s3_object")
    assert job.kwargs["key"].startswith("avatars/")


async def test_patch_avatar_key_not_owned_by_caller_is_404_UPLOAD_NOT_FOUND(
    player_client, make_player, client_for, db_session, fake_storage
) -> None:
    other = await make_player()
    other_user = await db_session.get(User, other.owner_user_id)
    other_key = await _presigned_key(client_for(other_user))
    fake_storage.seed(other_key, PNG, "image/png")
    r = await player_client.patch(ME, json={"avatar_key": other_key})
    assert r.status_code == 404 and r.json()["error"]["code"] == "UPLOAD_NOT_FOUND"
    assert other_key in fake_storage.objects  # not touched


@pytest.mark.parametrize("key", ["avatars/x/y.png", "quarantine/../etc/passwd", "nonexistent"])
async def test_patch_avatar_key_outside_own_quarantine_is_404(player_client, key) -> None:
    r = await player_client.patch(ME, json={"avatar_key": key})
    assert r.status_code == 404 and r.json()["error"]["code"] == "UPLOAD_NOT_FOUND"


async def test_patch_avatar_key_never_uploaded_is_404(player_client) -> None:
    key = await _presigned_key(player_client)
    assert (await player_client.patch(ME, json={"avatar_key": key})).status_code == 404


async def test_patch_avatar_with_png_header_but_gif_bytes_is_422_UPLOAD_INVALID(
    player_client, fake_storage
) -> None:
    key = await _presigned_key(player_client)
    fake_storage.seed(key, GIF, "image/png")  # client lied about the content type
    r = await player_client.patch(ME, json={"avatar_key": key})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UPLOAD_INVALID"
    assert fake_storage.keys("quarantine/") == [] and fake_storage.keys("avatars/") == []


async def test_patch_avatar_oversized_object_is_422_and_cleaned_up(
    player_client, fake_storage
) -> None:
    key = await _presigned_key(player_client)
    fake_storage.seed(key, PNG + b"0" * (5 * 1024 * 1024), "image/png")
    r = await player_client.patch(ME, json={"avatar_key": key})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UPLOAD_INVALID"
    assert fake_storage.keys("quarantine/") == []


async def test_avatar_null_clears_avatar(player_client, fake_storage, fake_queue) -> None:
    key = await _presigned_key(player_client)
    fake_storage.seed(key, PNG, "image/png")
    await player_client.patch(ME, json={"avatar_key": key})
    r = await player_client.patch(ME, json={"avatar_key": None})
    assert r.json()["avatar_url"] is None and len(fake_queue.named("delete_s3_object")) == 1


async def test_email_change_requires_current_password_and_keeps_old_email_until_confirmed(
    player_client, fake_queue, client, db_session
) -> None:
    body = {"new_email": "fresh@x.co", "current_password": "wrong-password-123"}
    bad = await player_client.post(f"{ME}/email-change", json=body)
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert fake_queue.jobs == []

    ok = await player_client.post(
        f"{ME}/email-change", json={**body, "current_password": TEST_PASSWORD}
    )
    assert ok.status_code == 202
    [job] = fake_queue.named("send_email_change_email")
    assert job.kwargs["new_email"] == "fresh@x.co" and job.kwargs["user_id"] == str(
        player_client.user.id
    )
    user = await db_session.get(User, player_client.user.id, populate_existing=True)
    assert user.email == player_client.user.email  # unchanged until confirmed

    confirm = await client.post("/api/v1/auth/verify-email", json={"token": job.kwargs["token"]})
    assert confirm.status_code == 204
    user = await db_session.get(User, player_client.user.id, populate_existing=True)
    assert user.email == "fresh@x.co"
    row = (await db_session.execute(select(OneTimeToken))).scalar_one()
    assert row.kind.value == "email_change" and row.payload == {"new_email": "fresh@x.co"}


async def test_email_change_to_taken_email_is_409_and_child_is_403(
    player_client, child_client, make_user
) -> None:
    await make_user(email="taken@x.co")
    body = {"new_email": "taken@x.co", "current_password": TEST_PASSWORD}
    taken = await player_client.post(f"{ME}/email-change", json=body)
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "EMAIL_TAKEN"
    child = await child_client.post(
        f"{ME}/email-change", json={"new_email": "k@x.co", "current_password": TEST_PASSWORD}
    )
    assert child.status_code == 403


async def test_response_never_contains_raw_s3_keys_or_hashes(player_client, fake_storage) -> None:
    key = await _presigned_key(player_client)
    fake_storage.seed(key, PNG, "image/png")
    text = (await player_client.patch(ME, json={"avatar_key": key})).text
    for forbidden in ("avatar_key", "password_hash", "token_hash", "logo_key", "quarantine"):
        assert forbidden not in text


async def test_me_requires_authentication(client) -> None:
    for method in ("get", "patch"):
        assert (await getattr(client, method)(ME)).status_code == 401
