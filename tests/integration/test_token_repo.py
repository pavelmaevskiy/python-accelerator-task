from datetime import timedelta

import pytest

from app.repositories.token_repo import TokenRepo

pytestmark = pytest.mark.integration


async def test_one_time_token_is_consumed_exactly_once(db_session, make_user, clock) -> None:
    u = await make_user()
    repo = TokenRepo(db_session)
    await repo.issue_one_time(u.id, "password_reset", "hash1", clock.now() + timedelta(hours=1))
    first = await repo.consume_one_time("hash1", now=clock.now())
    second = await repo.consume_one_time("hash1", now=clock.now())
    assert first is not None and second is None


async def test_expired_one_time_token_cannot_be_consumed(db_session, make_user, clock) -> None:
    u = await make_user()
    repo = TokenRepo(db_session)
    await repo.issue_one_time(u.id, "email_verify", "h2", clock.now() - timedelta(seconds=1))
    assert await repo.consume_one_time("h2", now=clock.now()) is None


async def test_issuing_new_token_of_kind_invalidates_previous(db_session, make_user, clock) -> None:
    u = await make_user()
    repo = TokenRepo(db_session)
    exp = clock.now() + timedelta(hours=1)
    await repo.issue_one_time(u.id, "password_reset", "old", exp)
    await repo.issue_one_time(u.id, "password_reset", "new", exp)
    assert await repo.consume_one_time("old", now=clock.now()) is None
    assert await repo.consume_one_time("new", now=clock.now()) is not None


async def test_consume_respects_kind_filter(db_session, make_user, clock) -> None:
    u = await make_user()
    repo = TokenRepo(db_session)
    await repo.issue_one_time(u.id, "email_verify", "k1", clock.now() + timedelta(hours=1))
    assert await repo.consume_one_time("k1", now=clock.now(), kinds=["password_reset"]) is None
    assert await repo.consume_one_time("k1", now=clock.now(), kinds=["email_verify"]) is not None


async def test_revoke_family_marks_all_tokens(db_session, make_user, clock) -> None:
    u = await make_user()
    repo = TokenRepo(db_session)
    fam = await repo.create_refresh(
        u.id, family_id=None, token_hash="r1", expires_at=clock.now() + timedelta(days=30)
    )
    await repo.create_refresh(
        u.id, family_id=fam.family_id, token_hash="r2", expires_at=clock.now() + timedelta(days=30)
    )
    await repo.revoke_family(fam.family_id, now=clock.now())
    assert (await repo.get_refresh_by_hash("r1")).revoked_at is not None
    assert (await repo.get_refresh_by_hash("r2")).revoked_at is not None


async def test_revoke_all_for_user_touches_every_family(db_session, make_user, clock) -> None:
    u = await make_user()
    repo = TokenRepo(db_session)
    for h in ("a", "b"):
        await repo.create_refresh(
            u.id, family_id=None, token_hash=h, expires_at=clock.now() + timedelta(days=1)
        )
    await repo.revoke_all_for_user(u.id, now=clock.now())
    assert (await repo.get_refresh_by_hash("a")).revoked_at is not None
    assert (await repo.get_refresh_by_hash("b")).revoked_at is not None
