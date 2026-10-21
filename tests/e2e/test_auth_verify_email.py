from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.security import hash_token
from app.models.identity import OneTimeToken, User

pytestmark = pytest.mark.integration

VERIFY = "/api/v1/auth/verify-email"
RESEND = "/api/v1/auth/verify-email/resend"


@pytest.fixture
async def unverified(make_user):
    return await make_user(email="new@x.co", verified=False)


async def test_verify_email_marks_user_verified_and_returns_204(
    client, unverified, issue_token, db_session
) -> None:
    token = await issue_token(unverified, "email_verify")
    r = await client.post(VERIFY, json={"token": token})
    assert r.status_code == 204
    user = await db_session.get(User, unverified.id, populate_existing=True)
    assert user.is_email_verified is True and user.status.value == "active"


async def test_verified_user_can_then_log_in(client, unverified, issue_token) -> None:
    from tests.conftest import TEST_PASSWORD

    body = {"email": "new@x.co", "password": TEST_PASSWORD}
    assert (await client.post("/api/v1/auth/login", json=body)).status_code == 403
    await client.post(VERIFY, json={"token": await issue_token(unverified, "email_verify")})
    assert (await client.post("/api/v1/auth/login", json=body)).status_code == 200


async def test_verify_email_token_is_single_use_422_token_invalid(
    client, unverified, issue_token
) -> None:
    token = await issue_token(unverified, "email_verify")
    assert (await client.post(VERIFY, json={"token": token})).status_code == 204
    r = await client.post(VERIFY, json={"token": token})
    assert r.status_code == 422 and r.json()["error"]["code"] == "TOKEN_INVALID"


async def test_expired_unknown_consumed_token_all_collapse_to_422_TOKEN_INVALID(
    client, unverified, issue_token
) -> None:
    expired = await issue_token(unverified, "email_verify", ttl=timedelta(seconds=-5))
    wrong_kind = await issue_token(unverified, "password_reset")
    for token in (expired, "u" * 43, wrong_kind):
        r = await client.post(VERIFY, json={"token": token})
        assert r.status_code == 422 and r.json()["error"]["code"] == "TOKEN_INVALID"


async def test_email_change_token_applies_pending_email_on_same_endpoint(
    client, make_user, issue_token, db_session
) -> None:
    user = await make_user(email="old@x.co", verified=False)
    token = await issue_token(user, "email_change", payload={"new_email": "fresh@x.co"})
    assert (await client.post(VERIFY, json={"token": token})).status_code == 204
    refreshed = await db_session.get(User, user.id, populate_existing=True)
    assert refreshed.email == "fresh@x.co" and refreshed.is_email_verified is True


async def test_email_change_to_email_taken_meanwhile_is_409_and_token_survives(
    client, make_user, issue_token, db_session
) -> None:
    user = await make_user(email="old@x.co")
    token = await issue_token(user, "email_change", payload={"new_email": "taken@x.co"})
    await make_user(email="taken@x.co")
    r = await client.post(VERIFY, json={"token": token})
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_TAKEN"
    row = (
        await db_session.execute(
            select(OneTimeToken).where(OneTimeToken.token_hash == hash_token(token))
        )
    ).scalar_one()
    assert row.consumed_at is None  # rolled back with the failed transaction


async def test_short_token_is_422_validation(client) -> None:
    r = await client.post(VERIFY, json={"token": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_resend_always_202_for_known_and_unknown_email(client, unverified) -> None:
    known = await client.post(RESEND, json={"email": "new@x.co"})
    unknown = await client.post(RESEND, json={"email": "ghost@x.co"})
    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content == b""


async def test_resend_enqueues_only_for_unverified_existing_user_and_invalidates_old_token(
    client, make_user, unverified, issue_token, fake_queue, db_session
) -> None:
    old = await issue_token(unverified, "email_verify")
    await make_user(email="verified@x.co", verified=True)
    await client.post(RESEND, json={"email": "ghost@x.co"})
    await client.post(RESEND, json={"email": "verified@x.co"})
    assert fake_queue.jobs == []

    await client.post(RESEND, json={"email": "NEW@x.co"})
    [job] = fake_queue.named("send_verification_email")
    assert job.kwargs["user_id"] == str(unverified.id)
    new_token = job.kwargs["token"]
    assert (await client.post(VERIFY, json={"token": old})).status_code == 422
    assert (await client.post(VERIFY, json={"token": new_token})).status_code == 204


async def test_resend_token_lives_48_hours(
    client, unverified, fake_queue, clock, db_session
) -> None:
    await client.post(RESEND, json={"email": "new@x.co"})
    token = fake_queue.jobs[0].kwargs["token"]
    row = (
        await db_session.execute(
            select(OneTimeToken).where(OneTimeToken.token_hash == hash_token(str(token)))
        )
    ).scalar_one()
    assert row.expires_at == clock.now() + timedelta(hours=48)


async def test_fourth_resend_in_an_hour_is_429(client, unverified) -> None:
    for _ in range(3):
        assert (await client.post(RESEND, json={"email": "new@x.co"})).status_code == 202
    r = await client.post(RESEND, json={"email": "new@x.co"})
    assert r.status_code == 429 and "Retry-After" in r.headers
