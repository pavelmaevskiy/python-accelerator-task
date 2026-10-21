"""Shared fixtures.

Integration/e2e tests use real commits against the ``trainer_test`` PostgreSQL database and a
dedicated Redis db; every table is truncated and Redis flushed after each test.
"""

import os
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.clock import FixedClock
from app.core.config import get_settings
from app.core.enums import UserRole, UserStatus
from app.core.ids import new_id
from app.core.security import PasswordHasher
from app.models.identity import (
    CoachMembership,
    CoachProfile,
    PlayerProfile,
    Profile,
    TrainerPlayerLink,
    TrainerProfile,
    User,
)

ROOT = Path(__file__).resolve().parent.parent
TEST_PASSWORD = "correct horse battery staple"  # noqa: S105
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
CHEAP_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture(scope="session")
def _migrated() -> Iterator[None]:
    """Recreate the schema from scratch with ``alembic upgrade head`` once per session."""
    import asyncio

    url = get_settings().database_url

    async def _reset() -> None:
        eng = create_async_engine(url, poolclass=NullPool)
        async with eng.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await eng.dispose()

    asyncio.run(_reset())
    cfg = Config(str(ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")
    yield


@pytest_asyncio.fixture
async def engine(_migrated: None) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(get_settings().database_url, poolclass=NullPool)
    yield eng
    async with eng.begin() as conn:
        tables = (
            await conn.execute(
                text(
                    "select string_agg(quote_ident(tablename), ',') from pg_tables "
                    "where schemaname='public' and tablename <> 'alembic_version'"
                )
            )
        ).scalar_one()
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await eng.dispose()


@pytest.fixture
def sessionmaker_(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_() as session:
        yield session


@pytest_asyncio.fixture
async def redis() -> AsyncIterator[Any]:
    from redis.asyncio import Redis

    r = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 10, 21, 12, 0, tzinfo=UTC))


# --- factories (write through the ORM and commit) -------------------------------------------

MakeUser = Callable[..., Any]


@pytest.fixture
def make_user(db_session: AsyncSession) -> Callable[..., Any]:
    async def _make(
        *,
        role: str = "player",
        email: str | None = None,
        password: str = TEST_PASSWORD,
        verified: bool = True,
        status: str = "active",
        first_name: str = "Test",
        last_name: str = "User",
        timezone: str = "UTC",
        must_change_password: bool = False,
    ) -> User:
        uid = new_id()
        user = User(
            id=uid,
            email=email or f"user-{uid}@example.com",
            password_hash=CHEAP_HASHER.hash(password),
            role=UserRole(role),
            status=UserStatus(status),
            is_email_verified=verified,
            must_change_password=must_change_password,
        )
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            Profile(user_id=uid, first_name=first_name, last_name=last_name, timezone=timezone)
        )
        await db_session.commit()
        return user

    return _make


@pytest.fixture
def make_trainer(make_user: Callable[..., Any], db_session: AsyncSession) -> Callable[..., Any]:
    async def _make(
        *, slug: str | None = None, business_name: str = "Acme Tennis", **kw: Any
    ) -> User:
        user = await make_user(role="trainer", **kw)
        db_session.add(
            TrainerProfile(
                user_id=user.id,
                business_name=business_name,
                slug=slug or f"acme-{str(user.id)[-12:]}",
            )
        )
        await db_session.commit()
        return user

    return _make


@pytest.fixture
def make_player(make_user: Callable[..., Any], db_session: AsyncSession) -> Callable[..., Any]:
    """Create a player user plus (by default) their own adult player profile."""

    async def _make(
        *,
        birth_date: Any = None,
        own_profile: bool = True,
        **kw: Any,
    ) -> Any:
        from datetime import date

        user = await make_user(role="player", **kw)
        profile = None
        if own_profile:
            profile = PlayerProfile(
                owner_user_id=user.id,
                display_name="Test Player",
                birth_date=birth_date or date(1990, 1, 1),
                gender="other",
                is_dependent=False,
            )
            db_session.add(profile)
            await db_session.commit()
        return profile if profile is not None else user

    return _make


@pytest.fixture
def make_coach(
    make_user: Callable[..., Any], make_trainer: Callable[..., Any], db_session: AsyncSession
) -> Callable[..., Any]:
    async def _make(*, trainer: User | None = None, **kw: Any) -> User:
        coach = await make_user(role="coach", **kw)
        db_session.add(CoachProfile(user_id=coach.id))
        await db_session.flush()
        t = trainer or await make_trainer()
        db_session.add(CoachMembership(coach_user_id=coach.id, trainer_id=t.id, is_active=True))
        await db_session.commit()
        return coach

    return _make


@pytest.fixture
def link_player(db_session: AsyncSession) -> Callable[..., Any]:
    async def _link(trainer: User, player_profile: PlayerProfile, status: str = "active") -> Any:
        link = TrainerPlayerLink(
            trainer_id=trainer.id,
            player_profile_id=player_profile.id,
            source="admin",
            status=status,
        )
        db_session.add(link)
        await db_session.commit()
        return link

    return _link
