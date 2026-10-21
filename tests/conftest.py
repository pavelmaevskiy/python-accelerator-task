"""Shared fixtures.

Integration/e2e tests use real commits against the ``trainer_test`` PostgreSQL database and a
dedicated Redis db; every table is truncated and Redis flushed after each test.
"""

import os
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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
from tests.fakes import FakeQueue, MutableClock

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
def clock() -> MutableClock:
    return MutableClock()


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


# --- application wiring ---------------------------------------------------------------------


@pytest.fixture
def settings() -> Any:
    from app.core.config import Settings

    return Settings(
        env="test",
        jwt_keys={"k1": "k" * 40},
        jwt_current_kid="k1",
        ip_hash_salt="test-salt",
        redis_url=TEST_REDIS_URL,
        public_base_url="http://testserver",
    )


@pytest.fixture
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest_asyncio.fixture
async def container(
    settings: Any,
    engine: AsyncEngine,
    sessionmaker_: async_sessionmaker[AsyncSession],
    redis: Any,
    clock: MutableClock,
    fake_queue: FakeQueue,
) -> Any:
    from app.core.container import Container
    from app.core.rate_limit import RateLimiter
    from app.core.security import TokenCodec

    return Container(
        settings=settings,
        engine=engine,
        sessionmaker=sessionmaker_,
        redis=redis,
        hasher=CHEAP_HASHER,
        codec=TokenCodec(settings.jwt_keys, settings.jwt_current_kid),
        clock=clock,
        queue=fake_queue,
        rate_limiter=RateLimiter(redis),
    )


@pytest.fixture
def app(container: Any) -> Any:
    from app.main import create_app

    return create_app(container)


@pytest_asyncio.fixture
async def client(app: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        yield c


@pytest.fixture
def token_for(container: Any) -> Callable[..., str]:
    from datetime import timedelta

    def _token(
        user: User,
        *,
        act: Any = None,
        jti: str | None = None,
        ttl: timedelta = timedelta(minutes=15),
    ) -> str:
        return container.codec.encode_access(
            sub=str(user.id),
            role=user.role.value if hasattr(user.role, "value") else str(user.role),
            trainer_id=None,
            ttl=ttl,
            act=str(act) if act else None,
            jti=jti,
        )

    return _token


@pytest.fixture
def auth(token_for: Callable[..., str]) -> Callable[..., dict[str, str]]:
    def _headers(user: User, **kw: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {token_for(user, **kw)}"}

    return _headers


# --- auth helpers ---------------------------------------------------------------------------


@dataclass
class LoggedIn:
    user: User
    access_token: str
    refresh_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@pytest.fixture
def login_as(client: Any) -> Callable[..., Any]:
    async def _login(user: User, password: str = TEST_PASSWORD) -> LoggedIn:
        r = await client.post(
            "/api/v1/auth/login", json={"email": user.email, "password": password}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        return LoggedIn(user, body["access_token"], body["refresh_token"])

    return _login


@pytest_asyncio.fixture
async def logged_in(make_user: Callable[..., Any], login_as: Callable[..., Any]) -> LoggedIn:
    return cast(LoggedIn, await login_as(await make_user()))


@pytest.fixture
def audit_rows(db_session: AsyncSession) -> Callable[..., Any]:
    from sqlalchemy import func, select

    from app.models.admin import AuditLog

    async def _count(*, action: str) -> int:
        stmt = select(func.count()).select_from(AuditLog).where(AuditLog.action == action)
        return int((await db_session.execute(stmt)).scalar_one())

    return _count


@pytest.fixture
def deactivate(db_session: AsyncSession) -> Callable[..., Any]:
    from sqlalchemy import update

    async def _deactivate(user: User) -> None:
        await db_session.execute(update(User).where(User.id == user.id).values(status="inactive"))
        await db_session.commit()

    return _deactivate
