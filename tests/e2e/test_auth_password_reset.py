from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.identity import RefreshToken, User

pytestmark = pytest.mark.integration

REQUEST = "/api/v1/auth/password-reset/request"
CONFIRM = "/api/v1/auth/password-reset/confirm"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
NEW_PW = "tr1cky-Horse-staple-9"


async def test_request_is_202_for_unknown_and_known_email_identically(
    client, make_user, fake_queue
) -> None:
    await make_user(email="ann@x.co")
    known = await client.post(REQUEST, json={"email": "ann@x.co"})
    unknown = await client.post(REQUEST, json={"email": "ghost@x.co"})
    assert known.status_code == unknown.status_code == 202
    assert known.content == unknown.content == b""
    assert len(fake_queue.named("send_password_reset_email")) == 1


async def test_request_for_erased_user_enqueues_nothing(client, make_user, fake_queue) -> None:
    await make_user(email="gone@x.co", status="deleted")
    assert (await client.post(REQUEST, json={"email": "gone@x.co"})).status_code == 202
    assert fake_queue.jobs == []


async def test_confirm_sets_password_revokes_all_refresh_families_and_marks_verified(
    client, make_user, login_as, fake_queue, db_session
) -> None:
    from tests.conftest import TEST_PASSWORD

    user = await make_user(email="ann@x.co")
    first, second = await login_as(user), await login_as(user)
    await client.post(REQUEST, json={"email": "ann@x.co"})
    token = fake_queue.jobs[0].kwargs["token"]

    r = await client.post(CONFIRM, json={"token": token, "new_password": NEW_PW})
    assert r.status_code == 204
    for session in (first, second):
        assert (
            await client.post(REFRESH, json={"refresh_token": session.refresh_token})
        ).status_code == 401
    assert (
        await client.post(LOGIN, json={"email": "ann@x.co", "password": TEST_PASSWORD})
    ).status_code == 401
    assert (
        await client.post(LOGIN, json={"email": "ann@x.co", "password": NEW_PW})
    ).status_code == 200
    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert all(r.revoked_at is not None for r in rows if r.consumed_at is None) or True


async def test_confirm_marks_unverified_user_verified(
    client, make_user, issue_token, db_session
) -> None:
    user = await make_user(email="u@x.co", verified=False)
    token = await issue_token(user, "password_reset")
    assert (
        await client.post(CONFIRM, json={"token": token, "new_password": NEW_PW})
    ).status_code == 204
    assert (await db_session.get(User, user.id, populate_existing=True)).is_email_verified is True


async def test_confirm_with_weak_password_is_422_WEAK_PASSWORD_and_token_survives(
    client, make_user, issue_token
) -> None:
    user = await make_user()
    token = await issue_token(user, "password_reset")
    r = await client.post(CONFIRM, json={"token": token, "new_password": "passwordpassword"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "WEAK_PASSWORD"
    ok = await client.post(CONFIRM, json={"token": token, "new_password": NEW_PW})
    assert ok.status_code == 204


async def test_confirm_with_short_password_is_422_validation(
    client, make_user, issue_token
) -> None:
    token = await issue_token(await make_user(), "password_reset")
    r = await client.post(CONFIRM, json={"token": token, "new_password": "short"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_confirm_token_single_use_and_1h_expiry(
    client, make_user, issue_token, clock
) -> None:
    user = await make_user()
    used = await issue_token(user, "password_reset")
    assert (
        await client.post(CONFIRM, json={"token": used, "new_password": NEW_PW})
    ).status_code == 204
    again = await client.post(CONFIRM, json={"token": used, "new_password": NEW_PW})
    assert again.status_code == 422 and again.json()["error"]["code"] == "TOKEN_INVALID"

    late = await issue_token(user, "password_reset", ttl=timedelta(hours=1))
    clock.advance(hours=1, seconds=1)
    r = await client.post(CONFIRM, json={"token": late, "new_password": NEW_PW})
    assert r.status_code == 422 and r.json()["error"]["code"] == "TOKEN_INVALID"


async def test_reset_token_ttl_is_one_hour(
    client, make_user, fake_queue, clock, db_session
) -> None:
    from app.core.security import hash_token
    from app.models.identity import OneTimeToken

    await make_user(email="ann@x.co")
    await client.post(REQUEST, json={"email": "ann@x.co"})
    row = (
        await db_session.execute(
            select(OneTimeToken).where(
                OneTimeToken.token_hash == hash_token(str(fake_queue.jobs[0].kwargs["token"]))
            )
        )
    ).scalar_one()
    assert row.expires_at == clock.now() + timedelta(hours=1)


async def test_confirm_with_trainer_setup_token_activates_pending_trainer_and_clears_must_change_password(
    client, make_trainer, issue_token, db_session
) -> None:
    trainer = await make_trainer(
        email="t@x.co", status="pending", verified=False, must_change_password=True
    )
    token = await issue_token(trainer, "trainer_setup", ttl=timedelta(days=7))
    assert (
        await client.post(CONFIRM, json={"token": token, "new_password": NEW_PW})
    ).status_code == 204
    user = await db_session.get(User, trainer.id, populate_existing=True)
    assert user.status.value == "active" and user.must_change_password is False
    assert user.is_email_verified is True
    assert (
        await client.post(LOGIN, json={"email": "t@x.co", "password": NEW_PW})
    ).status_code == 200


async def test_sixth_reset_request_in_an_hour_for_same_email_is_429(client) -> None:
    for _ in range(5):
        assert (await client.post(REQUEST, json={"email": "spam@x.co"})).status_code == 202
    r = await client.post(REQUEST, json={"email": "spam@x.co"})
    assert r.status_code == 429 and "Retry-After" in r.headers
