from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import hash_token
from app.models.identity import OneTimeToken, Profile, TrainerProfile, User

pytestmark = pytest.mark.integration

URL = "/api/v1/admin/users/trainers"
PW = "tr1cky-Horse-staple-9"


def body(**kw: object) -> dict[str, object]:
    base = {
        "email": "newtrainer@x.co",
        "first_name": "Tia",
        "last_name": "Lee",
        "business_name": "Tia Tennis",
        "slug": "tia-tennis",
    }
    return {**base, **kw}


async def test_provision_creates_user_profile_trainer_profile_pending_must_change_password(
    admin_client, db_session
) -> None:
    r = await admin_client.post(URL, json=body(phone="+15551234567"))
    assert r.status_code == 201
    out = r.json()
    assert out["status"] == "pending" and out["slug"] == "tia-tennis"
    assert out["business_name"] == "Tia Tennis" and out["email"] == "newtrainer@x.co"
    user = (
        await db_session.execute(select(User).where(User.email == "newtrainer@x.co"))
    ).scalar_one()
    assert user.role.value == "trainer" and user.status.value == "pending"
    assert user.must_change_password is True and user.is_email_verified is False
    profile = await db_session.get(Profile, user.id)
    assert (profile.first_name, profile.phone) == ("Tia", "+15551234567")
    tp = await db_session.get(TrainerProfile, user.id)
    assert (tp.business_name, tp.slug) == ("Tia Tennis", "tia-tennis")


async def test_response_is_pending_and_never_contains_password(admin_client, db_session) -> None:
    r = await admin_client.post(URL, json=body())
    text = r.text.lower()
    assert "password" not in text and "token" not in text and "hash" not in text
    assert set(r.json()) == {"id", "email", "business_name", "slug", "status"}
    user = (
        await db_session.execute(select(User).where(User.email == "newtrainer@x.co"))
    ).scalar_one()
    assert user.password_hash.startswith("$argon2id$")  # random and unusable, never exposed


async def test_setup_token_ttl_is_7_days_and_invite_email_is_enqueued_after_commit(
    admin_client, fake_queue, clock, db_session
) -> None:
    await admin_client.post(URL, json=body())
    [job] = fake_queue.named("send_trainer_invite_email")
    row = (
        await db_session.execute(
            select(OneTimeToken).where(
                OneTimeToken.token_hash == hash_token(str(job.kwargs["token"]))
            )
        )
    ).scalar_one()
    assert row.kind.value == "trainer_setup" and row.expires_at == clock.now() + timedelta(days=7)


async def test_duplicate_email_409_EMAIL_TAKEN_and_duplicate_slug_409_SLUG_TAKEN(
    admin_client, make_user, make_trainer, fake_queue
) -> None:
    await make_user(email="taken@x.co")
    await make_trainer(slug="taken-slug")
    email = await admin_client.post(URL, json=body(email="TAKEN@x.co"))
    assert email.status_code == 409 and email.json()["error"]["code"] == "EMAIL_TAKEN"
    slug = await admin_client.post(URL, json=body(slug="taken-slug"))
    assert slug.status_code == 409 and slug.json()["error"]["code"] == "SLUG_TAKEN"
    assert fake_queue.jobs == []


@pytest.mark.parametrize("slug", ["Bad Slug", "a", "-x-", "UPPER", "a_b", "x" * 64, "double--dash"])
async def test_invalid_slug_422(admin_client, slug) -> None:
    assert (await admin_client.post(URL, json=body(slug=slug))).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "nope"},
        {"first_name": ""},
        {"business_name": ""},
        {"phone": "5"},
        {"role": "trainer"},
    ],
)
async def test_invalid_payloads_422(admin_client, payload) -> None:
    assert (await admin_client.post(URL, json=body(**payload))).status_code == 422


async def test_failure_after_user_insert_rolls_back_all_rows(
    admin_client, make_trainer, db_count, fake_queue
) -> None:
    await make_trainer(slug="clash")
    users, profiles, trainers = (
        await db_count(User),
        await db_count(Profile),
        await db_count(TrainerProfile),
    )
    r = await admin_client.post(
        URL, json=body(slug="clash")
    )  # user row is flushed before this fails
    assert r.status_code == 409
    assert (await db_count(User), await db_count(Profile), await db_count(TrainerProfile)) == (
        users, profiles, trainers,
    )  # fmt: skip
    assert await db_count(OneTimeToken) == 0 and fake_queue.jobs == []


async def test_trainer_can_complete_setup_via_password_reset_confirm_and_then_login(
    client, admin_client, fake_queue
) -> None:
    await admin_client.post(URL, json=body())
    token = fake_queue.named("send_trainer_invite_email")[0].kwargs["token"]
    early = await client.post(
        "/api/v1/auth/login", json={"email": "newtrainer@x.co", "password": PW}
    )
    assert early.status_code == 401  # pending: no usable credentials yet
    done = await client.post(
        "/api/v1/auth/password-reset/confirm", json={"token": token, "new_password": PW}
    )
    assert done.status_code == 204
    login = await client.post(
        "/api/v1/auth/login", json={"email": "newtrainer@x.co", "password": PW}
    )
    assert login.status_code == 200
    user = login.json()["user"]
    assert user["status"] == "active" and user["must_change_password"] is False


async def test_provisioning_writes_an_audit_row_without_pii(
    admin_client, db_session, audit_rows
) -> None:
    from app.models.admin import AuditLog

    await admin_client.post(URL, json=body())
    assert await audit_rows(action="trainer.created") == 1
    row = (await db_session.execute(select(AuditLog))).scalars().all()[-1]
    assert row.actor_user_id == admin_client.user.id and row.details == {"slug": "tia-tennis"}


async def test_non_admins_get_403(non_trainer_client, trainer_client) -> None:
    for c in (non_trainer_client, trainer_client):
        if c.user.role.value != "super_admin":
            assert (await c.post(URL, json=body())).status_code == 403
