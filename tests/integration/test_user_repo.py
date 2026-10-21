from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.core.enums import UserRole, UserStatus
from app.models.identity import User
from app.repositories.user_repo import UserFilter, UserRepo

pytestmark = pytest.mark.integration


async def test_get_auth_state_returns_status_role_verified(db_session, make_user) -> None:
    u = await make_user(role="coach", verified=False, status="pending")
    state = await UserRepo(db_session).get_auth_state(u.id)
    assert (state.status, state.role, state.is_email_verified) == (
        UserStatus.PENDING,
        UserRole.COACH,
        False,
    )
    assert await UserRepo(db_session).get_auth_state(uuid4()) is None


async def test_get_by_email_is_case_insensitive(db_session, make_user) -> None:
    u = await make_user(email="Ann@Example.com")
    found = await UserRepo(db_session).get_by_email("ann@example.COM")
    assert found is not None and found.id == u.id


async def test_list_page_sorts_desc_and_returns_limit_plus_one(db_session, make_user) -> None:
    users = [await make_user() for _ in range(5)]
    for i, u in enumerate(users):
        await db_session.execute(
            update(User)
            .where(User.id == u.id)
            .values(created_at=datetime(2026, 1, 1 + i, tzinfo=UTC))
        )
    await db_session.commit()
    repo = UserRepo(db_session)
    rows = await repo.list_page(UserFilter(), None, 3)
    assert len(rows) == 4  # limit + 1 signals has_more
    assert [r.user.id for r in rows[:3]] == [u.id for u in reversed(users)][:3]
    last = rows[2].user
    nxt = await repo.list_page(UserFilter(), (last.created_at, last.id), 3)
    assert [r.user.id for r in nxt] == [users[1].id, users[0].id]


async def test_list_page_filters_by_role_status_query_and_tenant(
    db_session, make_user, make_trainer, make_coach, make_player, link_player
) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer=trainer, first_name="Zed")
    player = await make_player()
    await link_player(trainer, player)
    outsider = await make_user(email="outsider@example.com", status="inactive")
    repo = UserRepo(db_session)

    by_role = await repo.list_page(UserFilter(role=UserRole.COACH), None, 50)
    assert [r.user.id for r in by_role] == [coach.id]
    assert by_role[0].trainer_id == trainer.id and by_role[0].business_name == "Acme Tennis"

    by_status = await repo.list_page(UserFilter(status=UserStatus.INACTIVE), None, 50)
    assert [r.user.id for r in by_status] == [outsider.id]

    by_query = await repo.list_page(UserFilter(query="zed"), None, 50)
    assert [r.user.id for r in by_query] == [coach.id]
    assert await repo.list_page(UserFilter(query="100%"), None, 50) == []

    tenant = await repo.list_page(UserFilter(trainer_id=trainer.id), None, 50)
    assert {r.user.id for r in tenant} == {trainer.id, coach.id, player.owner_user_id}
