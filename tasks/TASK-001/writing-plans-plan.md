# Epic-01 Identity, Tenancy & Auth API Implementation Plan

**Task:** TASK-001

> **For Claude:** Use `git-worktrees` to create isolated workspace, then implement with `coder` skill.

**Goal:** Build the Epic-01 JSON API (auth, me, trainer, public, admin: 44 operations) exactly as specified in `specs/api-designer-spec.md`, with OpenAPI docs generated from the code.

**Architecture:** Greenfield single FastAPI service, layer-first (`app/{api,services,repositories,models,jobs,core,db}`), route -> service -> repository -> model. Services own transactions; role guards are router dependencies; cross-tenant misses are 404; one error envelope `{error:{code,message,details}}`. Binding decisions: corrections C1-C8 in `specs/architect-architecture.md` win over `tasks/TASK-001/brainstorm-design.md`; API deviations D1-D3 and open items O1-O6 in `specs/api-designer-spec.md` are implemented as written.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic v2, SQLAlchemy 2.0 async (asyncpg), Alembic, PostgreSQL 16 (citext, btree_gist, pg_trgm), Redis 7, ARQ, argon2-cffi, PyJWT, zxcvbn, boto3 (behind a Protocol), defusedxml, structlog, pytest + pytest-asyncio + httpx, ruff, mypy.

---

## Source documents (read before starting)

| Doc | Use |
|-----|-----|
| `specs/api-designer-spec.md` | The HTTP contract: paths, schemas, status/error codes. Copy schema code from here verbatim. |
| `specs/architect-architecture.md` | Module placement, C1-C8 corrections, principal resolution, uploads, erasure, approvals seam. |
| `tasks/TASK-001/brainstorm-design.md` | Data model columns (superseded by C1-C8 where they conflict). |
| `Task/Epics/00_Backend_Conventions.md` | Stack, layering, tenancy, error/pagination/idempotency conventions. |
| `.claude/DOD.md`, `AGENTS.md` | Definition of Done, no `--no-verify`, run tests + lint before claiming done. |

## Ground rules for the implementer

- The repo has **no application code yet**. Task 1 creates it. `app/` lives at repo root next to `specs/` and `tasks/`.
- TDD every task: failing test, see it fail, minimal code, see it pass, commit. Run `uv run ruff check . && uv run mypy app && uv run pytest` before each commit once Task 1 is done.
- Integration tests need PostgreSQL 16 and Redis 7. Use `docker compose up -d db redis` (Task 1 creates `docker-compose.yml`). Unit tests must not need them.
- Never commit `.env` or secrets. Never use `--no-verify`.
- Routers never import `repositories` or `models`. Services never import `fastapi`. Repositories never commit.
- Every request model extends `StrictModel` (`extra="forbid"`); every response model extends `ORMModel`. No ORM object is ever returned from a route.
- Conflicts between the spec and the epic text are already resolved in the spec (D1-D3, O1-O6). Do not re-litigate; if something is unclear, stop and escalate to `/architect`.
- Commit messages: Conventional Commits, one commit per task (more if a task is large).

## Milestones

| Milestone | Tasks | Outcome |
|-----------|-------|---------|
| M1 Foundation | 1-6 | App boots, error envelope, config, security primitives, DB + models + migration, test fixtures |
| M2 Auth | 7-11 | login, refresh, logout, verify, resend, reset, principal + guards, rate limit |
| M3 Registration and share links | 12-13 | register, trainer share-links, public preview |
| M4 Me | 14-18 | profile, avatar, email-change, availability, contexts, players, approvals |
| M5 Trainer, public, admin | 19-23 | branding, admin users, provisioning, impersonation, erasure |
| M6 Hardening | 24-26 | OpenAPI metadata test, permission matrix + tenancy e2e, DoD verify |

---

## M1 Foundation

### Task 1: Project scaffold and health check

**Files:**
- Create: `pyproject.toml`, `docker-compose.yml`, `.env.example`, `app/__init__.py`, `app/main.py`, `tests/conftest.py`, `tests/unit/test_health.py`
- Modify: `.gitignore` (add `.env`, `.venv/`, `.mypy_cache/`, `.ruff_cache/`, `.pytest_cache/`)

**Step 1: Write the failing test**

`tests/unit/test_health.py`
```python
from httpx import ASGITransport, AsyncClient

from app.main import create_app


async def test_health_returns_ok() -> None:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_health.py`
Expected: FAIL (ModuleNotFoundError: app) -- first run `uv sync` after writing `pyproject.toml`.

**Step 3: Write minimal implementation**

`pyproject.toml` (key parts):
```toml
[project]
name = "trainer-platform"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115", "uvicorn[standard]", "pydantic>=2.8", "pydantic-settings", "email-validator",
  "sqlalchemy[asyncio]>=2.0.30", "asyncpg", "alembic", "redis>=5", "arq", "argon2-cffi", "pyjwt",
  "zxcvbn", "uuid-utils", "boto3", "defusedxml", "structlog", "httpx", "tzdata",
]

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "pytest-cov", "ruff", "mypy", "types-boto3", "freezegun"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = ["integration: needs postgres+redis"]

[tool.ruff]
line-length = 100
target-version = "py312"
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "S", "ASYNC"]
[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "S105", "S106"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["app"]
plugins = ["pydantic.mypy"]
```

`app/main.py`
```python
from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Trainer Platform API", version="1.0.0")

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

`docker-compose.yml`: services `db` (postgres:16, env `POSTGRES_PASSWORD=dev`, db `trainer_test`, port 5432), `redis` (redis:7, port 6379).

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_health.py && uv run ruff check . && uv run mypy app`
Expected: PASS, no lint or type errors.

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock docker-compose.yml .env.example .gitignore app tests
git commit -m "chore: scaffold FastAPI project with health check"
```

---

### Task 2: Domain errors and the error envelope

**Files:**
- Create: `app/core/__init__.py`, `app/core/errors.py`, `app/api/__init__.py`, `app/api/errors.py`, `app/api/schemas.py`
- Modify: `app/main.py`
- Test: `tests/unit/test_error_envelope.py`

**Step 1: Write the failing test**

```python
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.errors import register_exception_handlers
from app.core.errors import (
    AuthenticationError, ConflictError, NotFoundError, PermissionDeniedError, RateLimitedError,
)


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    class Body(BaseModel):
        n: int

    @app.get("/e/{kind}")
    async def raise_it(kind: str) -> None:
        raise {
            "401": AuthenticationError("INVALID_CREDENTIALS", "bad"),
            "403": PermissionDeniedError("FORBIDDEN", "no"),
            "404": NotFoundError("NOT_FOUND", "gone"),
            "409": ConflictError("EMAIL_TAKEN", "dup", details={"action": "login_then_redeem"}),
            "429": RateLimitedError("RATE_LIMITED", "slow", retry_after=30),
        }[kind]

    @app.post("/v")
    async def validate(b: Body) -> None: ...

    return app


async def test_domain_errors_map_to_status_and_envelope() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        for kind, status in [("401", 401), ("403", 403), ("404", 404), ("409", 409), ("429", 429)]:
            r = await c.get(f"/e/{kind}")
            assert r.status_code == status
            assert set(r.json()["error"]) == {"code", "message", "details"}
        assert (await c.get("/e/429")).headers["Retry-After"] == "30"
        assert (await c.get("/e/409")).json()["error"]["details"] == {"action": "login_then_redeem"}


async def test_pydantic_validation_is_422_with_errors_list() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.post("/v", json={"n": "x"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["loc"] == ["body", "n"]
```

**Step 2:** Run `uv run pytest tests/unit/test_error_envelope.py` -- Expected: FAIL (ImportError).

**Step 3: Implement**

`app/core/errors.py`: base `DomainError(Exception)` with `status_code: ClassVar[int]`, `code`, `message`, `details: dict[str, Any] | None`; subclasses `AuthenticationError` (401), `PermissionDeniedError` (403), `NotFoundError` (404), `ConflictError` (409), `DomainValidationError` (422), `RateLimitedError` (429, extra `retry_after: int`). Constructor signature `(code: str, message: str, details: dict | None = None)`.

`app/api/schemas.py`: `StrictModel`, `ORMModel`, `ErrorBody`, `ErrorResponse`, `Page[T]`, `CursorParams`, and the `Password`/`HexColor`/`Timezone`/`Slug` annotated types, copied from the spec section "Shared base schemas". `validate_iana_tz` raises `ValueError` unless `value in zoneinfo.available_timezones()`.

`app/api/errors.py`: `register_exception_handlers(app)` registers handlers for `DomainError` (envelope, `Retry-After` header when `RateLimitedError`), `RequestValidationError` (422 `VALIDATION_ERROR`, `details={"errors": [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]}` -- never echo `input` or `ctx`), and `StarletteHTTPException` (map to envelope, code from status). Unhandled exceptions: 500 envelope `INTERNAL_ERROR`, no stack trace.

`app/main.py`: call `register_exception_handlers(app)` in `create_app`.

**Step 4:** Run `uv run pytest tests/unit/test_error_envelope.py` -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(core): domain errors and single error envelope handler"
```

---

### Task 3: Settings, clock, cursor pagination

**Files:**
- Create: `app/core/config.py`, `app/core/clock.py`, `app/core/pagination.py`
- Test: `tests/unit/test_pagination.py`, `tests/unit/test_clock.py`

**Step 1: Write the failing tests**

```python
# tests/unit/test_pagination.py
import pytest
from datetime import UTC, datetime
from uuid import uuid4

from app.core.errors import DomainValidationError
from app.core.pagination import decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    ts, uid = datetime(2026, 10, 21, tzinfo=UTC), uuid4()
    assert decode_cursor(encode_cursor(ts, uid)) == (ts, uid)


def test_bad_cursor_is_422() -> None:
    with pytest.raises(DomainValidationError) as e:
        decode_cursor("not-base64!!")
    assert e.value.code == "INVALID_CURSOR"
```
```python
# tests/unit/test_clock.py
from datetime import date

from app.core.clock import FixedClock, age_on


def test_age_on_exact_birthday_boundaries() -> None:
    assert age_on(date(2010, 10, 21), date(2026, 10, 20)) == 15
    assert age_on(date(2010, 10, 21), date(2026, 10, 21)) == 16
    assert age_on(date(2008, 2, 29), date(2026, 2, 28)) == 17
```

**Step 2:** Run `uv run pytest tests/unit` -- Expected: FAIL (ImportError).

**Step 3: Implement**
- `config.py`: `Settings(BaseSettings)` with `database_url`, `redis_url`, `jwt_keys: dict[str, str]`, `jwt_current_kid`, `access_ttl_seconds=900`, `refresh_ttl_days=30`, `ip_hash_salt`, `s3_bucket`, `public_base_url`, `env`. Cached `get_settings()`. Load from env / `.env` (never commit `.env`).
- `clock.py`: `Clock` Protocol (`now() -> datetime`, `today() -> date`), `SystemClock`, `FixedClock`, and pure `age_on(birth: date, today: date) -> int` (subtract 1 if (month, day) not yet reached; Feb-29 births turn a year older on Mar 1 in non-leap years).
- `pagination.py`: `encode_cursor(created_at, id) -> str` (urlsafe base64 of JSON), `decode_cursor` raising `DomainValidationError("INVALID_CURSOR", ...)`.

**Step 4:** Run `uv run pytest tests/unit` -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(core): settings, clock with exact age, cursor pagination"
```

---

### Task 4: Security primitives (argon2id, JWT, opaque tokens)

**Files:**
- Create: `app/core/security.py`
- Test: `tests/unit/test_security.py`

**Step 1: Write the failing test**

```python
from datetime import timedelta

import pytest

from app.core.errors import AuthenticationError
from app.core.security import PasswordHasher, TokenCodec, generate_opaque_token, hash_token


def test_hash_and_verify_password() -> None:
    h = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)  # cheap params in tests
    digest = h.hash("correct horse battery")
    assert digest.startswith("$argon2id$")
    assert h.verify(digest, "correct horse battery") is True
    assert h.verify(digest, "wrong") is False


def test_verify_unknown_user_burns_dummy_hash() -> None:
    h = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    assert h.verify(None, "anything") is False  # dummy hash path, never raises


def test_production_params_are_argon2id_t3_m64mib_p4() -> None:
    h = PasswordHasher()
    assert (h.time_cost, h.memory_cost, h.parallelism) == (3, 65536, 4)


def test_jwt_round_trip_and_kid_rotation() -> None:
    old = TokenCodec({"k1": "a" * 32}, current_kid="k1")
    token = old.encode_access(sub="u1", role="player", trainer_id=None, ttl=timedelta(minutes=15))
    rotated = TokenCodec({"k1": "a" * 32, "k2": "b" * 32}, current_kid="k2")
    claims = rotated.decode_access(token)  # previous key still accepted
    assert claims["sub"] == "u1" and claims["typ"] == "access" and "jti" in claims


def test_expired_or_tampered_jwt_is_401() -> None:
    c = TokenCodec({"k1": "a" * 32}, current_kid="k1")
    expired = c.encode_access(sub="u", role="player", trainer_id=None, ttl=timedelta(seconds=-1))
    with pytest.raises(AuthenticationError):
        c.decode_access(expired)
    with pytest.raises(AuthenticationError):
        c.decode_access(expired[:-2] + "xx")


def test_opaque_token_is_43_chars_and_hash_is_stable() -> None:
    t = generate_opaque_token()
    assert len(t) == 43
    assert hash_token(t) == hash_token(t) and hash_token(t) != t
```

**Step 2:** Run `uv run pytest tests/unit/test_security.py` -- Expected: FAIL (ImportError).

**Step 3: Implement** `app/core/security.py`
- `PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)` wrapping `argon2.PasswordHasher` (type ID). `verify(digest: str | None, password) -> bool`: if `digest is None` verify against a precomputed dummy hash and return False; catch `VerifyMismatchError`/`InvalidHashError`. `hash(password) -> str`. Add `needs_rehash`.
- `TokenCodec(keys, current_kid)`: HS256, header `kid`; `encode_access(sub, role, trainer_id, ttl, act=None, jti=None)` claims `sub, role, trainer_id, act, jti (uuid4), typ="access", iat, exp`; `decode_access` looks up key by header `kid` (unknown kid -> `AuthenticationError("INVALID_TOKEN")`), requires `exp`, `typ == "access"`.
- `generate_opaque_token()` = `secrets.token_urlsafe(32)` (43 chars); `hash_token()` = hex SHA-256.
- Note in a docstring: callers run `PasswordHasher.verify/hash` via `anyio.to_thread.run_sync`.

**Step 4:** Run `uv run pytest tests/unit/test_security.py` -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(core): argon2id hasher, JWT codec with kid rotation, opaque tokens"
```

---

### Task 5: DB base, identity models, Alembic migration

**Files:**
- Create: `app/db/__init__.py`, `app/db/base.py`, `app/db/session.py`, `app/models/__init__.py`, `app/models/identity.py`, `app/models/admin.py`, `app/models/core.py`, `app/core/ids.py`, `alembic.ini`, `app/db/alembic/env.py`, `app/db/alembic/versions/0001_epic01_identity.py`
- Test: `tests/integration/test_schema.py`

**Step 1: Write the failing test** (marker `integration`)

```python
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


async def test_all_epic01_tables_exist(db_session) -> None:
    rows = await db_session.execute(text("select tablename from pg_tables where schemaname='public'"))
    names = {r[0] for r in rows}
    assert {
        "users", "profiles", "trainer_profiles", "coach_profiles", "coach_memberships",
        "player_profiles", "trainer_player_links", "share_links", "share_link_events",
        "availability_windows", "purchase_approvals", "impersonation_sessions", "user_erasures",
        "refresh_tokens", "one_time_tokens", "audit_log", "feature_flags", "idempotency_keys",
    } <= names


async def test_coach_can_have_only_one_active_membership(db_session, make_user, make_trainer) -> None:
    coach = await make_user(role="coach")
    t1, t2 = await make_trainer(), await make_trainer()
    await db_session.execute(text(
        "insert into coach_memberships (id, coach_user_id, trainer_id, is_active) values (gen_random_uuid(), :c, :t, true)"), {"c": coach.id, "t": t1.id})
    with pytest.raises(IntegrityError):
        await db_session.execute(text(
            "insert into coach_memberships (id, coach_user_id, trainer_id, is_active) values (gen_random_uuid(), :c, :t, true)"), {"c": coach.id, "t": t2.id})


async def test_overlapping_availability_rejected_but_adjacent_allowed(db_session, make_player) -> None:
    p = await make_player()
    ins = text("insert into availability_windows (id, player_profile_id, weekday, start_time, end_time, is_available) "
               "values (gen_random_uuid(), :p, 1, :s, :e, true)")
    await db_session.execute(ins, {"p": p.id, "s": "16:00", "e": "18:00"})
    await db_session.execute(ins, {"p": p.id, "s": "18:00", "e": "20:00"})  # half-open: allowed
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.execute(ins, {"p": p.id, "s": "17:00", "e": "19:00"})


async def test_users_email_is_case_insensitive_unique(db_session, make_user) -> None:
    await make_user(email="A@b.co")
    with pytest.raises(IntegrityError):
        await make_user(email="a@B.co")
```

Add the fixtures first (this task also creates them): `tests/conftest.py` gains an session-scoped engine that runs `alembic upgrade head` against `trainer_test`, a `db_session` fixture (connection-level transaction rolled back after each test, `join_transaction_mode="create_savepoint"`), and factory fixtures `make_user`, `make_trainer`, `make_player`, `make_coach` writing through the ORM models.

**Step 2:** Run `docker compose up -d db redis && uv run pytest -m integration tests/integration/test_schema.py` -- Expected: FAIL (no models / migration).

**Step 3: Implement**
- `app/core/ids.py`: `new_id() -> UUID` using `uuid_utils.uuid7()` converted to `uuid.UUID`.
- `app/db/base.py`: `Base(DeclarativeBase)` with naming convention; mixin `TimestampMixin` (`created_at`, `updated_at`, `timestamptz`, `server_default=func.now()`). `session.py`: `create_engine`, `async_sessionmaker(expire_on_commit=False)`.
- `app/models/identity.py`: typed `Mapped[...]` models for every table in `specs/architect-architecture.md` "Module placement" + brainstorm "Data Model", **applying C1-C5**:
  - C1 `coach_profiles` (user_id PK, bio, credentials, certifications JSONB, is_public) + `coach_memberships` with `uq_coach_one_active_trainer`.
  - C2 only `CHECK (birth_date > '1900-01-01')`.
  - C3 `one_time_tokens` (kinds `email_verify`, `password_reset`, `trainer_setup`, `email_change`; `token_hash` UNIQUE; `payload` JSONB; `ix_one_time_tokens_user_kind ... WHERE consumed_at IS NULL`).
  - C4 `availability_windows` with `player_profile_id`/`coach_user_id`, `CHECK (num_nonnulls(...) = 1)`, `CHECK (start_time < end_time)`, generated `span` column and two partial `EXCLUDE USING gist` constraints.
  - All FKs `ondelete="RESTRICT"`; `users.email` is `CITEXT`; enums as PG enums.
- `app/models/admin.py`: `AuditLog`, `FeatureFlag`. `app/models/core.py`: `IdempotencyKey` (`key`, `principal_id`, `request_hash`, `response_status`, `response_body` JSONB, `created_at`; UNIQUE `(principal_id, key)`).
- Alembic migration `0001_epic01_identity.py` (hand-reviewed, not blind autogenerate): first `CREATE EXTENSION IF NOT EXISTS citext, btree_gist, pg_trgm`, `CREATE TYPE timerange AS RANGE (subtype = time)`, then tables, then indexes including `ix_users_created_id`, `ix_tpl_player (player_profile_id, status)`, trigram GIN on `users.email` and on `profiles.first_name/last_name`, UNIQUE `ix_impersonation_jti`.

**Step 4:** Run `uv run pytest -m integration tests/integration/test_schema.py` -- Expected: PASS. Then `uv run alembic downgrade base && uv run alembic upgrade head` -- Expected: both succeed (downgrade must work).

**Step 5: Commit**
```bash
git add app tests alembic.ini
git commit -m "feat(db): identity models and migration with tenancy, coach and availability constraints"
```

---

### Task 6: Repository base conventions, user and token repositories

**Files:**
- Create: `app/repositories/__init__.py`, `app/repositories/user_repo.py`, `app/repositories/token_repo.py`
- Test: `tests/integration/test_token_repo.py`, `tests/integration/test_user_repo.py`

**Step 1: Write the failing tests**

```python
# tests/integration/test_token_repo.py
import pytest
from datetime import timedelta

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


async def test_revoke_family_marks_all_tokens(db_session, make_user, clock) -> None:
    u = await make_user()
    repo = TokenRepo(db_session)
    fam = await repo.create_refresh(u.id, family_id=None, token_hash="r1", expires_at=clock.now() + timedelta(days=30))
    await repo.revoke_family(fam.family_id, now=clock.now())
    assert (await repo.get_refresh_by_hash("r1")).revoked_at is not None
```

`test_user_repo.py`: `get_auth_state(user_id)` returns `(status, role, is_email_verified)` with one query; `get_by_email` is case-insensitive; `list_page(filter, cursor)` sorts `created_at desc, id desc` and returns `limit+1` rows for `has_more` detection.

**Step 2:** Run `uv run pytest -m integration tests/integration/test_token_repo.py tests/integration/test_user_repo.py` -- Expected: FAIL (ImportError).

**Step 3: Implement**
- `consume_one_time` = single atomic `UPDATE one_time_tokens SET consumed_at=:now WHERE token_hash=:h AND consumed_at IS NULL AND expires_at > :now RETURNING *`.
- `issue_one_time` first marks earlier unconsumed tokens of the same `(user_id, kind)` consumed, then inserts.
- `create_refresh(user_id, family_id|None, ...)` generates a new `family_id` when None. `rotate` helpers lock with `SELECT ... FOR UPDATE` on the token hash row.
- Repos accept an `AsyncSession` in the constructor, never commit.

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(repo): user and token repositories with atomic one-time token consumption"
```

---

## M2 Auth

### Task 7: Rate limiter and dependency wiring (`deps.py`, principal, guards)

**Files:**
- Create: `app/core/rate_limit.py`, `app/api/deps.py`, `app/services/__init__.py`
- Modify: `app/main.py` (lifespan: engine, redis, settings, hasher, codec on `app.state`)
- Test: `tests/integration/test_rate_limit.py`, `tests/integration/test_principal.py`

**Step 1: Write the failing tests**

```python
# tests/integration/test_rate_limit.py
import pytest

from app.core.errors import RateLimitedError
from app.core.rate_limit import RateLimiter

pytestmark = pytest.mark.integration


async def test_fixed_window_blocks_after_limit_and_reports_retry_after(redis) -> None:
    rl = RateLimiter(redis)
    for _ in range(5):
        await rl.hit("login:email:a@b.co", limit=5, window_seconds=60)
    with pytest.raises(RateLimitedError) as e:
        await rl.hit("login:email:a@b.co", limit=5, window_seconds=60)
    assert 0 < e.value.retry_after <= 60
```
```python
# tests/integration/test_principal.py  (uses a tiny app mounting a /whoami route with current_principal)
async def test_missing_or_garbage_bearer_is_401(client) -> None: ...
async def test_deactivated_user_is_rejected_on_next_request(client, login_as, db_session) -> None:
    # login_as returns a valid access token; set users.status='inactive'; same token -> 401
async def test_require_role_returns_403_for_wrong_role(client, token_for) -> None: ...
async def test_act_claim_without_active_session_is_401_impersonation_expired(client, forged_act_token) -> None: ...
async def test_x_trainer_context_for_player_must_match_active_link_else_404(client, token_for, link_player) -> None: ...
```
Write each test body fully (use `make_user`/`make_trainer`/`make_player` fixtures and a `token_for(user)` helper in `tests/conftest.py` that signs with the test `TokenCodec`).

**Step 2:** Run `uv run pytest -m integration tests/integration/test_rate_limit.py tests/integration/test_principal.py` -- Expected: FAIL.

**Step 3: Implement**
- `RateLimiter.hit(key, limit, window_seconds)`: Lua `INCR` + `EXPIRE` on first hit; raises `RateLimitedError("RATE_LIMITED", ..., retry_after=ttl)`.
- `deps.rate_limit(name)` returns a dependency using the table in the spec ("Rate limits"): `login` -> 10/min/IP and 5/min/email (email read from the already-parsed body via a small dependency), `password_reset` 5/h/email, `verify_resend` 3/h/email + per IP, `share_preview` 60/min/IP, default 300/min/principal.
- `Principal` frozen dataclass `(user_id, role, trainer_id, act, is_child, jti)`.
- `current_principal`: decode JWT -> `user_repo.get_auth_state` -> status must be `active` else 401 -> if `act`: `impersonation_repo.get_active(jti)` else 401 `IMPERSONATION_EXPIRED` -> resolve `trainer_id` (trainer: own id, coach: active membership, player: validated `X-Trainer-Context` against active `trainer_player_links`, mismatch -> 404). Sets `X-Impersonated-By` response header and writes the audit row in its own transaction when `act` is present.
- `require_role(*roles)` dependency factory -> 403 `FORBIDDEN`; `reject_impersonation` -> 403 `ADMIN_FORBIDDEN_WHILE_IMPERSONATING`; `require_tenant_scope` returns `trainer_id` from the principal only.
- Services get their dependencies through small provider functions in `deps.py` (`get_auth_service`, ...).

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(api): rate limiter, principal resolution, role and impersonation guards"
```

---

### Task 8: `POST /auth/login`

**Files:**
- Create: `app/services/auth_service.py`, `app/api/v1/__init__.py`, `app/api/v1/auth/__init__.py`, `app/api/v1/auth/routes.py`, `app/api/v1/auth/schemas.py`
- Modify: `app/main.py` (include `api_router`)
- Test: `tests/unit/test_auth_service_login.py`, `tests/e2e/test_auth_login.py`

**Step 1: Write the failing tests**

```python
# tests/e2e/test_auth_login.py
import pytest

pytestmark = pytest.mark.integration


async def test_login_success_returns_token_pair_and_user(client, make_user) -> None:
    await make_user(email="ann@x.co", password="correct horse battery staple", verified=True, status="active")
    r = await client.post("/api/v1/auth/login", json={"email": "ann@x.co", "password": "correct horse battery staple"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert body["user"]["email"] == "ann@x.co" and "password_hash" not in body["user"]


@pytest.mark.parametrize("email,password", [("nobody@x.co", "whatever-long-password"), ("ann@x.co", "wrong-password-123")])
async def test_unknown_user_and_bad_password_are_identical_401(client, make_user, email, password) -> None:
    await make_user(email="ann@x.co", password="correct horse battery staple", verified=True, status="active")
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_unverified_with_correct_password_is_403_without_tokens(client, make_user) -> None:
    await make_user(email="u@x.co", password="correct horse battery staple", verified=False, status="active")
    r = await client.post("/api/v1/auth/login", json={"email": "u@x.co", "password": "correct horse battery staple"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert "access_token" not in r.text


@pytest.mark.parametrize("status", ["inactive", "deleted", "pending"])
async def test_non_active_user_is_uniform_401(client, make_user, status) -> None: ...


async def test_sixth_attempt_for_same_email_is_429_with_retry_after(client) -> None: ...
async def test_extra_field_in_body_is_422(client) -> None: ...
```

Unit test `test_auth_service_login.py`: with a fake `UserRepo`/`TokenRepo`, assert `last_login_at` is set, a refresh row is created in a new family, and the hasher is invoked even when the user is unknown (spy on `PasswordHasher.verify` called with `None`).

**Step 2:** Run `uv run pytest tests/unit/test_auth_service_login.py && uv run pytest -m integration tests/e2e/test_auth_login.py` -- Expected: FAIL.

**Step 3: Implement**
- `schemas.py`: `TokenPair`, `UserSummary`, `LoginRequest`, `LoginResponse` copied from the spec.
- `AuthService.login(email, password, ip_hash, ua_hash)`: fetch user by email; `await anyio.to_thread.run_sync(hasher.verify, user.password_hash if user else None, password)`; if not ok -> `AuthenticationError("INVALID_CREDENTIALS")`; if `status != active` -> same 401; if not verified -> `PermissionDeniedError("EMAIL_NOT_VERIFIED")`; issue access JWT + opaque refresh (new family); set `last_login_at`; commit via `async with session.begin()`.
- Route: copy the router decorator from the spec (`summary`, `responses`, `dependencies=[Depends(rate_limit("login"))]`).
- `api/v1/__init__.py`: `api_router = APIRouter(prefix="/api/v1")` including the auth router (more in later tasks).

**Step 4:** Run the same commands -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(auth): POST /auth/login with constant-time failure path and verification gate"
```

---

### Task 9: `POST /auth/refresh` and `POST /auth/logout`

**Files:**
- Modify: `app/services/auth_service.py`, `app/api/v1/auth/routes.py`, `app/api/v1/auth/schemas.py`, `app/repositories/audit_repo.py` (create), `app/services/audit_service.py` (create)
- Test: `tests/e2e/test_auth_refresh.py`

**Step 1: Write the failing tests**

```python
async def test_refresh_rotates_and_old_token_becomes_consumed(client, logged_in) -> None:
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": logged_in.refresh_token})
    assert r.status_code == 200
    assert r.json()["refresh_token"] != logged_in.refresh_token


async def test_reusing_consumed_token_revokes_family_and_writes_audit(client, logged_in, audit_rows) -> None:
    first = (await client.post("/api/v1/auth/refresh", json={"refresh_token": logged_in.refresh_token})).json()
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": logged_in.refresh_token})
    assert r.status_code == 401 and r.json()["error"]["code"] == "REFRESH_REUSE_DETECTED"
    # the successor is now dead too
    r2 = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert r2.status_code == 401
    assert await audit_rows(action="refresh_reuse") == 1  # survives the failed request


async def test_unknown_or_expired_refresh_is_REFRESH_INVALID(client) -> None: ...
async def test_refresh_for_deactivated_user_is_401(client, logged_in, deactivate) -> None: ...
async def test_logout_revokes_family_and_is_idempotent(client, logged_in) -> None:
    h = {"Authorization": f"Bearer {logged_in.access_token}"}
    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": logged_in.refresh_token}, headers=h)).status_code == 204
    assert (await client.post("/api/v1/auth/logout", json={"refresh_token": logged_in.refresh_token}, headers=h)).status_code == 204
    assert (await client.post("/api/v1/auth/refresh", json={"refresh_token": logged_in.refresh_token})).status_code == 401
async def test_logout_without_access_token_is_401(client) -> None: ...
async def test_ten_parallel_refreshes_of_one_token_yield_one_success(client, logged_in) -> None: ...  # asyncio.gather
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_auth_refresh.py` -- Expected: FAIL (404 route).

**Step 3: Implement**
- `AuthService.refresh(token)`: in a transaction `SELECT ... FOR UPDATE` the row by hash. Not found / expired / revoked -> `REFRESH_INVALID`. `consumed_at` set -> revoke family, write the audit row via `AuditService.record_isolated` (**separate session/transaction, committed before raising**), raise `REFRESH_REUSE_DETECTED`. Otherwise stamp `consumed_at`, check user still `active`, insert successor in same family, issue new access token. Impersonation refresh: if the family belongs to an impersonation session past `expires_at` -> `IMPERSONATION_EXPIRED` (D3).
- `AuthService.logout(token)`: revoke family if found; always return None.
- Routes per spec (`logout` requires `current_principal`; returns 204 with empty body).

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(auth): refresh rotation with reuse detection, logout"
```

---

### Task 10: Email jobs seam and `POST /auth/verify-email`, `/verify-email/resend`

**Files:**
- Create: `app/core/email.py`, `app/jobs/__init__.py`, `app/jobs/identity_jobs.py`, `app/core/unit_of_work.py`
- Modify: `app/services/auth_service.py`, `app/api/v1/auth/routes.py`, `app/api/v1/auth/schemas.py`
- Test: `tests/unit/test_post_commit_enqueue.py`, `tests/e2e/test_auth_verify_email.py`

**Step 1: Write the failing tests**

```python
# tests/unit/test_post_commit_enqueue.py
async def test_job_is_enqueued_only_after_commit_and_dropped_on_rollback(fake_queue, uow_factory) -> None:
    uow = uow_factory()
    with pytest.raises(RuntimeError):
        async with uow:
            uow.enqueue("send_verification_email", user_id="u1", token="t")
            raise RuntimeError("boom")
    assert fake_queue.jobs == []
    async with uow_factory() as ok:
        ok.enqueue("send_verification_email", user_id="u1", token="t")
        assert fake_queue.jobs == []     # not yet
    assert [j.name for j in fake_queue.jobs] == ["send_verification_email"]
```
```python
# tests/e2e/test_auth_verify_email.py
async def test_verify_email_marks_user_verified_and_returns_204(client, unverified_user_with_token) -> None: ...
async def test_verify_email_token_is_single_use_422_token_invalid(client, ...) -> None: ...
async def test_expired_unknown_consumed_token_all_collapse_to_422_TOKEN_INVALID(client) -> None: ...
async def test_email_change_token_applies_pending_email_on_same_endpoint(client, ...) -> None: ...
async def test_resend_always_202_for_known_and_unknown_email(client) -> None: ...
async def test_resend_enqueues_only_for_unverified_existing_user_and_invalidates_old_token(client, fake_queue, ...) -> None: ...
async def test_fourth_resend_in_an_hour_is_429(client) -> None: ...
```

**Step 2:** Run `uv run pytest tests/unit/test_post_commit_enqueue.py && uv run pytest -m integration tests/e2e/test_auth_verify_email.py` -- Expected: FAIL.

**Step 3: Implement**
- `UnitOfWork` async context manager: wraps `session.begin()`, collects `enqueue(name, **kwargs)` calls, flushes them to the ARQ pool only after a successful commit.
- `EmailSender` Protocol (`send(template, to, context)`) + `LoggingEmailSender` dev adapter. Jobs in `identity_jobs.py`: `send_verification_email`, `send_welcome_email`, `send_password_reset_email`, `send_trainer_invite_email`, `send_coach_invite_email`, `send_email_change_email`, `send_approval_notification`. Services call only `uow.enqueue`; raw tokens are passed to the job, never logged (structlog redaction `token*`).
- `AuthService.verify_email(token)`: `consume_one_time` over kinds `email_verify` and `email_change`; no row -> `DomainValidationError("TOKEN_INVALID")`; `email_verify` sets `is_email_verified` (and activates `pending` is not done here); `email_change` applies `payload.new_email` (catch unique violation -> `409 EMAIL_TAKEN`) and marks verified.
- `AuthService.resend_verification(email)`: no-op unless user exists and is unverified; issues a 48 h token (invalidates older) and enqueues. Always returns None; route returns 202.

**Step 4:** Run the same commands -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(auth): email verification and resend with post-commit job enqueue"
```

---

### Task 11: `POST /auth/password-reset/request` and `/confirm` (incl. trainer setup)

**Files:**
- Modify: `app/services/auth_service.py`, `app/api/v1/auth/routes.py`, `app/api/v1/auth/schemas.py`
- Test: `tests/e2e/test_auth_password_reset.py`

**Step 1: Write the failing tests**

```python
async def test_request_is_202_for_unknown_and_known_email_identically(client, make_user) -> None: ...
async def test_confirm_sets_password_revokes_all_refresh_families_and_marks_verified(client, ...) -> None:
    # two logins -> two families; after confirm both refresh tokens are 401; user can log in with new password
async def test_confirm_with_weak_password_is_422_WEAK_PASSWORD(client, ...) -> None:   # "passwordpassword" fails zxcvbn >= 3
async def test_confirm_token_single_use_and_1h_expiry(client, clock, ...) -> None: ...
async def test_confirm_with_trainer_setup_token_activates_pending_trainer_and_clears_must_change_password(client, provisioned_trainer) -> None: ...
async def test_sixth_reset_request_in_an_hour_for_same_email_is_429(client) -> None: ...
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_auth_password_reset.py` -- Expected: FAIL.

**Step 3: Implement**
- `app/core/password_policy.py`: `check_password(password, user_inputs)` -> raises `DomainValidationError("WEAK_PASSWORD")` when `zxcvbn(password, user_inputs=...)["score"] < 3`. Run in a thread (zxcvbn is CPU heavy). Unit-test it separately in `tests/unit/test_password_policy.py` (fails first, then passes).
- `request_password_reset(email)`: when user exists and is not erased, issue 1 h `password_reset` token and enqueue; always 202.
- `confirm_password_reset(token, new_password)`: consume kinds `password_reset` and `trainer_setup`; validate policy; set new hash; `is_email_verified=True`; for `trainer_setup`, `status: pending -> active`, `must_change_password=False`; revoke all refresh families of the user.

**Step 4:** Run `uv run pytest tests/unit/test_password_policy.py && uv run pytest -m integration tests/e2e/test_auth_password_reset.py` -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(auth): password reset request/confirm and trainer setup completion"
```

---

## M3 Registration and share links

### Task 12: Share links (trainer CRUD and public preview)

**Files:**
- Create: `app/repositories/share_link_repo.py`, `app/services/sharelink_service.py`, `app/api/v1/trainer/{__init__,routes,schemas}.py`, `app/api/v1/public/{__init__,routes,schemas}.py`
- Test: `tests/unit/test_sharelink_rules.py`, `tests/e2e/test_trainer_share_links.py`, `tests/e2e/test_public_share_link.py`

**Step 1: Write the failing tests**

```python
async def test_create_player_static_link_has_no_expiry_and_no_max_uses(trainer_client) -> None: ...
async def test_create_coach_unique_requires_target_email_and_sets_7d_expiry_and_max_uses_1(trainer_client, fake_queue, clock) -> None:
    # 201, expires_at == clock.now()+7d, max_uses == 1, invite email job enqueued
async def test_coach_unique_without_email_or_static_with_email_is_422(trainer_client) -> None: ...
async def test_code_is_22_char_urlsafe(trainer_client) -> None: ...
async def test_list_is_cursor_paged_and_filtered_by_kind_and_is_active(trainer_client) -> None: ...
async def test_delete_deactivates_idempotently_204(trainer_client) -> None: ...
async def test_other_trainers_link_delete_is_404_not_403(trainer_client, other_trainer_link) -> None: ...
async def test_non_trainer_roles_get_403_on_all_trainer_share_link_routes(player_client, coach_client) -> None: ...
# public
async def test_preview_valid_link_exposes_only_branding_fields_and_records_opened_event(client, link) -> None: ...
@pytest.mark.parametrize("state", ["unknown", "inactive", "expired", "exhausted"])
async def test_preview_invalid_states_are_one_identical_404_SHARE_LINK_INVALID(client, state) -> None: ...
async def test_preview_61st_request_per_minute_is_429(client, link) -> None: ...
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_trainer_share_links.py tests/e2e/test_public_share_link.py` -- Expected: FAIL.

**Step 3: Implement**
- `sharelink_service.create(principal, payload)`: `code = secrets.token_urlsafe(16)`; apply the kind rules; `expires_at`/`max_uses` set server-side; for `coach_unique` enqueue the invite job post-commit. `list`, `deactivate` use `trainer_id=principal.trainer_id` on every repo call (no bare `get(id)`; a miss raises `NotFoundError`).
- `preview(code, ip_hash, ua_hash)`: validity helper `is_redeemable(link, now)` reused by registration (Task 13); insert `share_link_events(kind=opened)` with salted hashes only.
- Mount routers: trainer router `dependencies=[Depends(require_role("trainer"))]`; public router without auth; add `Cache-Control` on neither (only branding, Task 19).
- Schemas `ShareLinkResponse`, `CreateShareLink` (with `model_validator`), `ShareLinkPreview` per the spec. `url` is built from `settings.public_base_url`.

**Step 4:** Run the same command and the unit file -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(trainer): share-link issue/list/deactivate and public preview"
```

---

### Task 13: `POST /auth/register`

**Files:**
- Create: `app/services/registration_service.py`, `app/repositories/profile_repo.py`, `app/repositories/tenancy_repo.py`
- Modify: `app/api/v1/auth/routes.py`, `app/api/v1/auth/schemas.py`
- Test: `tests/e2e/test_auth_register.py`, `tests/integration/test_register_concurrency.py`

**Step 1: Write the failing tests**

```python
async def test_register_via_player_static_link_creates_user_profile_player_and_active_link(client, static_link) -> None:
    r = await client.post("/api/v1/auth/register", params={"share_code": static_link.code},
                          json={"email": "new@x.co", "password": "tr1cky-Horse-staple-9", "first_name": "A", "last_name": "B", "birth_date": "1990-01-01"})
    assert r.status_code == 201
    b = r.json()
    assert b["tokens"] is None and b["verification_required"] is True and b["linked_trainer_id"] == str(static_link.trainer_id)


async def test_register_via_coach_link_with_matching_email_issues_tokens_and_is_verified(client, coach_link) -> None: ...
async def test_coach_link_email_mismatch_is_403_LINK_EMAIL_MISMATCH(client, coach_link) -> None: ...
async def test_exactly_one_of_share_code_or_camp_submission_id_else_422(client) -> None: ...
async def test_camp_submission_id_is_409_CAMP_SUBMISSION_UNAVAILABLE_until_epic08(client) -> None: ...
async def test_unknown_inactive_expired_exhausted_link_is_404_SHARE_LINK_INVALID(client) -> None: ...
async def test_existing_email_unauthenticated_is_409_ACCOUNT_EXISTS_with_details_action(client, ...) -> None: ...
async def test_authenticated_existing_account_redeems_link_200_and_only_adds_missing_link(client, ...) -> None: ...
async def test_child_login_cannot_redeem_403_CHILD_CANNOT_REDEEM(client, child_token) -> None: ...
async def test_player_profile_ids_not_owned_is_404(client, ...) -> None: ...
async def test_coach_registration_creates_coach_membership_and_ignores_birth_date(client, ...) -> None: ...
async def test_failure_midway_leaves_zero_rows(client, break_welcome_enqueue, db_count) -> None: ...  # single transaction
async def test_weak_password_422_and_invalid_phone_422(client, ...) -> None: ...
async def test_register_records_registered_event(client, ...) -> None: ...
```
```python
# tests/integration/test_register_concurrency.py
async def test_20_parallel_redemptions_of_one_coach_unique_link_yield_exactly_one_success(app, coach_link) -> None:
    results = await asyncio.gather(*[register_one(app, coach_link, i) for i in range(20)])
    assert sorted(r.status_code for r in results) == [201] + [404] * 19

async def test_200_parallel_static_redemptions_lose_no_use_count_writes(app, static_link) -> None:
    ...  # all 201, link.use_count == 200

async def test_same_email_race_yields_one_201_and_rest_409_EMAIL_TAKEN(app, static_link) -> None: ...
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_auth_register.py tests/integration/test_register_concurrency.py` -- Expected: FAIL.

**Step 3: Implement** `registration_service.register(...)` as a single `UnitOfWork`:
1. Validate query exclusivity (`share_code` xor `camp_submission_id`); camp path raises `ConflictError("CAMP_SUBMISSION_UNAVAILABLE")` unless a handler is registered (empty Protocol registry, like approvals).
2. Resolve the link, redeem atomically (C8): `coach_unique` conditional `UPDATE ... RETURNING`, 0 rows -> `NotFoundError("SHARE_LINK_INVALID")`; `player_static` unconditional `use_count = use_count + 1`.
3. Kind rules: coach link requires email equality (`LINK_EMAIL_MISMATCH`), creates `coach_profiles` + `coach_memberships`, implicit verification; player link creates user (role `player`), `profiles`, own `player_profiles` (when `birth_date`), `trainer_player_links(source="share_link")`.
4. Authenticated caller path: reject child (`CHILD_CANNOT_REDEEM`), reject coach-link by non-coach (`WRONG_LINK_KIND`), link `player_profile_ids` only if owned (else 404), create only missing links, return 200.
5. Unauthenticated + existing email -> `409 ACCOUNT_EXISTS` with `details={"action": "login_then_redeem"}`; unique-violation race -> `409 EMAIL_TAKEN`.
6. Insert `share_link_events(registered)`; enqueue welcome + verification email (not for implicitly verified); issue tokens only when implicitly verified (D1).
Optional authentication on this route: a dependency `optional_principal` returning `None` when no `Authorization` header is present (a *malformed* header is still 401).

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(auth): ShareLink registration with atomic redemption and D1 token policy"
```

---

## M4 Me

### Task 14: `GET/PATCH /me`, `PUT /me/avatar`, `POST /me/email-change`

**Files:**
- Create: `app/core/storage.py`, `app/core/svg_sanitizer.py`, `app/services/user_service.py`, `app/api/v1/me/{__init__,routes,schemas}.py`, `app/api/v1/uploads.py` (shared `PresignRequest`/`PresignResponse`)
- Test: `tests/unit/test_svg_sanitizer.py`, `tests/unit/test_upload_validation.py`, `tests/e2e/test_me_profile.py`

**Step 1: Write the failing tests**

```python
# tests/unit/test_svg_sanitizer.py
def test_strips_script_foreignobject_on_attributes_and_external_href() -> None:
    dirty = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script><foreignObject/><a href="http://evil"><rect onclick="x()" width="1" height="1"/></a></svg>'
    clean = sanitize_svg(dirty).decode()
    assert "script" not in clean and "foreignObject" not in clean and "onclick" not in clean and "evil" not in clean

def test_unparsable_or_xxe_svg_raises_upload_invalid() -> None: ...
```
```python
# tests/e2e/test_me_profile.py
async def test_get_me_returns_profile_with_resolved_trainer_id(player_client) -> None: ...
async def test_patch_me_updates_allowed_fields_only(player_client) -> None: ...
@pytest.mark.parametrize("field", ["role", "email", "status"])
async def test_patch_me_rejects_forbidden_fields_with_422(player_client, field) -> None: ...
async def test_presign_returns_quarantine_key_and_300s_expiry(player_client, fake_storage) -> None: ...
async def test_presign_rejects_bad_content_type_and_over_5mb(player_client) -> None: ...
async def test_patch_avatar_key_confirms_checks_magic_bytes_copies_and_deletes_quarantine(player_client, fake_storage) -> None: ...
async def test_patch_avatar_key_not_owned_by_caller_is_404_UPLOAD_NOT_FOUND(player_client, fake_storage) -> None: ...
async def test_patch_avatar_with_png_header_but_gif_bytes_is_422_UPLOAD_INVALID(player_client, fake_storage) -> None: ...
async def test_email_change_requires_current_password_and_keeps_old_email_until_confirmed(player_client, fake_queue) -> None: ...
async def test_email_change_to_taken_email_is_409_and_child_is_403(player_client, child_client) -> None: ...
async def test_response_never_contains_raw_s3_keys_or_hashes(player_client) -> None: ...
```

**Step 2:** Run `uv run pytest tests/unit/test_svg_sanitizer.py tests/unit/test_upload_validation.py && uv run pytest -m integration tests/e2e/test_me_profile.py` -- Expected: FAIL.

**Step 3: Implement**
- `ObjectStorage` Protocol (`presign_put`, `head`, `get`, `put`, `copy`, `delete`) + `S3ObjectStorage` (boto3 in `anyio.to_thread`) + `FakeObjectStorage` in `tests/fakes.py`.
- Shared `UploadService.confirm(user_id, key, kind)`: `head` -> size cap (avatar 5 MB, logo 2 MB) -> magic bytes via `detect_image_type(bytes)` (jpeg `FFD8FF`, png `89504E47`, webp `RIFF....WEBP`, svg via sanitizer) -> copy to final random key -> delete quarantine key. Key must start with `quarantine/<user-scope>/`; foreign key -> `NotFoundError("UPLOAD_NOT_FOUND")`.
- `user_service.get_me`, `update_me` (confirm `avatar_key` when present), `request_email_change` (verify current password, reject child, 24 h `email_change` token with `payload.new_email`, enqueue to new address).
- Routes per spec. `avatar_url` returned as a presigned/public URL built from the stored key, never the key itself.

**Step 4:** Run the same commands -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(me): profile, two-phase avatar upload, email change"
```

---

### Task 15: Availability (`/me/availability`, `/me/players/{id}/availability`, `/trainer/players/{id}/availability`)

**Files:**
- Create: `app/repositories/availability_repo.py`, `app/services/availability_service.py`
- Modify: `app/api/v1/me/routes.py`, `app/api/v1/me/schemas.py`, `app/api/v1/trainer/routes.py`
- Test: `tests/unit/test_availability_validation.py`, `tests/e2e/test_availability.py`

**Step 1: Write the failing tests**

```python
# unit: pure overlap check at minute boundaries
@pytest.mark.parametrize("a,b,overlap", [
    (("16:00", "18:00"), ("15:59", "16:00"), False),
    (("16:00", "18:00"), ("15:59", "16:01"), True),
    (("16:00", "18:00"), ("18:00", "20:00"), False),
    (("16:00", "18:00"), ("17:59", "18:01"), True),
])
def test_half_open_overlap(a, b, overlap) -> None: ...

def test_start_must_be_before_end() -> None: ...   # AvailabilityWindowIn model_validator
```
```python
# e2e
async def test_put_replaces_whole_set_and_get_returns_it(player_client) -> None: ...
async def test_put_overlapping_windows_is_422_OVERLAPPING_WINDOWS_and_set_unchanged(player_client) -> None: ...
async def test_adjacent_windows_are_accepted(player_client) -> None: ...
async def test_more_than_100_windows_is_422(player_client) -> None: ...
async def test_coach_has_own_availability_trainer_and_admin_get_403(coach_client, trainer_client, admin_client) -> None: ...
async def test_child_login_may_set_own_availability(child_client) -> None: ...
async def test_parent_sets_dependent_availability_and_other_parents_dependent_is_404(parent_client, other_parent_dependent) -> None: ...
async def test_trainer_reads_only_actively_linked_players_other_tenant_is_404(trainer_client, foreign_player) -> None: ...
async def test_db_exclusion_constraint_backstops_a_race(db_session, ...) -> None: ...  # IntegrityError translated to 422
```

**Step 2:** Run `uv run pytest tests/unit/test_availability_validation.py && uv run pytest -m integration tests/e2e/test_availability.py` -- Expected: FAIL.

**Step 3: Implement** `AvailabilityService.replace(subject, windows)` in one transaction: validate overlaps in Python, delete the subject's rows, insert new ones, translate the gist `ExclusionViolation` to `DomainValidationError("OVERLAPPING_WINDOWS")`. Subject resolution: player = caller's own player profile (child: the profile where `login_user_id == caller`), coach = `coach_user_id`, dependent = owned profile or 404. Trainer read goes through `tenancy_repo.get_active_link(trainer_id, player_id)`. Schemas copied from the spec. Roles: `/me/availability` allowed for player and coach only (403 otherwise).

**Step 4:** Run the same commands -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(availability): weekly replace-set with half-open overlap detection"
```

---

### Task 16: `/me/contexts` and dependents CRUD (`/me/players*`)

**Files:**
- Create: `app/services/player_service.py`
- Modify: `app/repositories/profile_repo.py`, `app/repositories/tenancy_repo.py`, `app/api/v1/me/routes.py`, `app/api/v1/me/schemas.py`
- Create: `app/core/commitments.py` (`CommitmentChecker` Protocol + `NoCommitments` default)
- Test: `tests/unit/test_player_age_rules.py`, `tests/e2e/test_me_players.py`

**Step 1: Write the failing tests**

```python
# unit, uses FixedClock
@pytest.mark.parametrize("birth,today,ok", [
    ("2008-10-22", "2026-10-21", True),    # 17y 364d
    ("2008-10-21", "2026-10-21", False),   # turned 18 today -> out
    ("2025-10-22", "2026-10-21", False),   # < 1 year
    ("2025-10-21", "2026-10-21", True),    # exactly 1
])
def test_dependent_age_is_1_to_17_exact(birth, today, ok) -> None: ...
```
> The spec says "1-18". Treat the upper bound exactly as `specs/api-designer-spec.md` and C2 do: **1 <= age <= 18 inclusive of 18 until the 19th birthday is NOT reached**. Before coding, assert the boundary in a unit test against the spec wording (`INVALID_AGE` when `age_on() < 1 or age_on() > 18`) and adjust the parametrisation above accordingly; if the requirements doc and spec disagree, escalate to `/architect` rather than guessing.

```python
# e2e
async def test_contexts_lists_only_active_links_with_branding(player_client) -> None: ...
async def test_contexts_403_for_non_player(trainer_client) -> None: ...
async def test_create_dependent_201_links_requested_trainers_only_if_parent_is_linked(parent_client) -> None: ...
async def test_trainer_id_not_linked_to_parent_is_404(parent_client, foreign_trainer) -> None: ...
async def test_duplicate_dependent_409_unless_allow_duplicate_true(parent_client) -> None: ...
async def test_child_cannot_create_dependent_403_CHILD_CANNOT_CREATE_DEPENDENT(child_client) -> None: ...
async def test_list_players_is_cursor_paged_and_owner_scoped(parent_client, other_parent) -> None: ...
async def test_get_patch_other_parents_dependent_is_404(parent_client, other_parent_dependent) -> None: ...
async def test_patch_birth_date_revalidates_age_422_INVALID_AGE(parent_client) -> None: ...
async def test_patch_rejects_skill_level_field_422(parent_client) -> None: ...
async def test_delete_dependent_soft_deactivates_204_and_409_ACTIVE_COMMITMENTS_via_checker(parent_client, busy_checker) -> None: ...
async def test_add_trainer_201_new_200_existing_404_unlinked(parent_client, ...) -> None: ...
async def test_remove_trainer_sets_link_inactive_204_and_409_with_rsvp_ids_in_details(parent_client, busy_checker) -> None: ...
```

**Step 2:** Run `uv run pytest tests/unit/test_player_age_rules.py && uv run pytest -m integration tests/e2e/test_me_players.py` -- Expected: FAIL.

**Step 3: Implement** `PlayerService` using `Clock` for age (`INVALID_AGE`), duplicate detection on `(owner_user_id, lower(display_name), birth_date)`, link creation `source="admin"`/`"share_link"` per enum rules (use the owner's link source `share_link` when none better exists; document the choice in a code comment), `CommitmentChecker.active_for_player(player_id, trainer_id|None)` returning `list[UUID]`. All reads filter by `owner_user_id`; misses raise `NotFoundError`. `contexts` query joins `trainer_player_links` -> `trainer_profiles` -> `profiles` using `ix_tpl_player`.

**Step 4:** Run the same commands -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(me): trainer contexts and dependent player CRUD with exact-age validation"
```

---

### Task 17: Purchase approvals (`/me/approvals*`) and the subject-handler seam

**Files:**
- Create: `app/services/approval_service.py`, `app/repositories/approval_repo.py`, `app/core/idempotency.py`, `app/repositories/idempotency_repo.py`
- Modify: `app/api/v1/me/routes.py`, `app/api/v1/me/schemas.py`, `app/jobs/identity_jobs.py` (cron `expire_purchase_approvals`)
- Test: `tests/unit/test_approval_service.py`, `tests/e2e/test_me_approvals.py`, `tests/integration/test_approval_concurrency.py`

**Step 1: Write the failing tests**

```python
# unit with FakeHandler registered for "event_rsvp"
async def test_approve_calls_handler_then_sets_status_in_one_transaction(...) -> None: ...
async def test_subject_type_without_handler_is_409_APPROVAL_SUBJECT_UNAVAILABLE_and_status_unchanged(...) -> None: ...
async def test_deny_calls_on_denied_or_expired(...) -> None: ...
async def test_expiry_job_expires_only_pending_past_48h_and_is_idempotent(clock, ...) -> None: ...
```
```python
# e2e
async def test_list_defaults_to_pending_and_paginates_and_is_parent_scoped(parent_client, other_parent_approval) -> None: ...
async def test_approve_requires_idempotency_key_422(parent_client, pending) -> None: ...
async def test_approve_replay_with_same_key_returns_stored_response_without_second_handler_call(parent_client, pending, handler_spy) -> None: ...
async def test_approve_non_pending_is_409_APPROVAL_NOT_PENDING(parent_client, ...) -> None: ...
async def test_other_parents_approval_is_404(parent_client, other_parent_approval) -> None: ...
async def test_parent_note_over_500_chars_is_422(parent_client, pending) -> None: ...
```
```python
# integration
async def test_10_parallel_approves_with_same_key_run_handler_once_and_all_return_200(app, pending) -> None: ...
async def test_10_parallel_approves_with_different_keys_one_200_rest_409(app, pending) -> None: ...
```

**Step 2:** Run `uv run pytest tests/unit/test_approval_service.py && uv run pytest -m integration tests/e2e/test_me_approvals.py tests/integration/test_approval_concurrency.py` -- Expected: FAIL.

**Step 3: Implement**
- `ApprovalSubjectHandler` Protocol exactly as in `specs/architect-architecture.md`; `ApprovalHandlerRegistry` (`register`, `get`) held on `app.state`, empty in Epic-01.
- `approve`: idempotency lookup `(principal_id, key)` first (return stored response); then `SELECT ... FOR UPDATE` on the approval filtered by `parent_user_id`; status check; handler lookup; `await handler.on_approved(...)`; update; store the idempotency row with the response; all in one transaction. Same-key concurrent callers serialize on the `UNIQUE (principal_id, key)` insert.
- `Idempotency-Key` dependency: header required, 8-128 chars, else 422.
- Cron `expire_purchase_approvals`: set-based `UPDATE ... WHERE status='pending' AND expires_at < now()`; call `on_denied_or_expired` for each handler-backed row.

**Step 4:** Run the same commands -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(me): purchase approvals with idempotent approve/deny and handler seam"
```

---

### Task 18: M4 checkpoint

**Files:** none new.

**Step 1:** Run `uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest`
Expected: all green, no skipped integration tests.
**Step 2:** Run `uv run pytest --cov=app --cov-report=term-missing -m "not load"` and confirm services in M2-M4 are >= 90% covered; add tests for any uncovered branch.
**Step 3: Commit** only if tests were added: `git commit -m "test: close coverage gaps after M4"`.

---

## M5 Trainer, public, admin

### Task 19: Trainer branding (`/trainer/branding`, `PUT /trainer/branding/logo`, `/public/trainers/{slug}/branding`)

**Files:**
- Create: `app/services/branding_service.py`
- Modify: `app/api/v1/trainer/routes.py`, `app/api/v1/trainer/schemas.py`, `app/api/v1/public/routes.py`, `app/api/v1/public/schemas.py`
- Test: `tests/e2e/test_trainer_branding.py`, `tests/e2e/test_public_branding.py`

**Step 1: Write the failing tests**

```python
async def test_get_branding_returns_own_branding(trainer_client) -> None: ...
async def test_patch_updates_fields_and_invalid_hex_color_is_422(trainer_client) -> None: ...
async def test_logo_presign_allows_png_svg_webp_up_to_2mib_only(trainer_client) -> None: ...
async def test_logo_key_confirm_sanitises_svg_and_rejects_oversize_or_bad_magic(trainer_client, fake_storage) -> None: ...
async def test_logo_key_not_owned_is_404_UPLOAD_NOT_FOUND(trainer_client) -> None: ...
async def test_patch_deletes_public_branding_cache_key(trainer_client, redis) -> None: ...
async def test_coach_and_player_get_403_on_trainer_branding(coach_client, player_client) -> None: ...
# public
async def test_public_branding_exposes_exactly_four_fields_with_cache_control_300(client, trainer) -> None: ...
async def test_public_branding_is_read_through_redis_with_ttl_300(client, redis, trainer) -> None: ...
async def test_unknown_slug_is_404(client) -> None: ...
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_trainer_branding.py tests/e2e/test_public_branding.py` -- Expected: FAIL.

**Step 3: Implement** `branding_service` reusing `UploadService.confirm(kind="logo")` from Task 14; Redis key `public:branding:{slug}` with TTL 300 and deletion on PATCH; response header `Cache-Control: public, max-age=300`. `PUT /trainer/branding/logo` request `LogoPresignRequest` per spec.

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(trainer): branding, logo upload, cached public branding"
```

---

### Task 20: Admin users: list, get, patch, deactivate, reactivate

**Files:**
- Create: `app/api/v1/admin/{__init__,routes,schemas}.py`
- Modify: `app/services/user_service.py`, `app/repositories/user_repo.py`, `app/services/audit_service.py`
- Test: `tests/e2e/test_admin_users.py`, `tests/integration/test_admin_users_explain.py`

**Step 1: Write the failing tests**

```python
async def test_list_filters_by_role_status_trainer_and_query(admin_client, seeded_users) -> None: ...
async def test_list_cursor_pagination_has_more_and_next_cursor_and_bad_cursor_422(admin_client, seeded_users) -> None: ...
async def test_list_sorted_created_at_desc_id_desc(admin_client, seeded_users) -> None: ...
async def test_all_admin_routes_403_for_every_non_admin_role_and_for_impersonated_admin(client_factory) -> None: ...  # parametrise over routes
async def test_get_unknown_user_404(admin_client) -> None: ...
async def test_patch_user_updates_allowed_fields_and_rejects_role_status_422(admin_client) -> None: ...
async def test_patch_erased_user_409_USER_ERASED(admin_client, erased_user) -> None: ...
async def test_deactivate_is_idempotent_revokes_refresh_and_blocks_next_request(admin_client, user_client) -> None: ...
async def test_reactivate_restores_active_but_not_deleted_409_USER_ERASED(admin_client, ...) -> None: ...
async def test_every_mutation_writes_an_audit_row(admin_client, audit_rows) -> None: ...
# integration
async def test_list_over_10k_users_uses_index_scan_not_seq_scan(db_session) -> None: ...  # EXPLAIN (FORMAT JSON) inspected; seed via generate_series
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_admin_users.py tests/integration/test_admin_users_explain.py` -- Expected: FAIL.

**Step 3: Implement** admin router with `dependencies=[Depends(require_role("super_admin")), Depends(reject_impersonation)]` and `AdminUserFilter`. Repo `list_page` uses keyset `(created_at, id) < (:c, :i)`; `query` uses `ILIKE` with the trigram indexes on email and profile names. `AuditService.record(actor, action, target, details)` called inside each service transaction. `AdminUserResponse` copied from the spec.

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(admin): user directory and lifecycle endpoints with audit"
```

---

### Task 21: `POST /admin/users/trainers`

**Files:**
- Modify: `app/services/user_service.py`, `app/api/v1/admin/routes.py`, `app/api/v1/admin/schemas.py`
- Test: `tests/e2e/test_admin_provision_trainer.py`

**Step 1: Write the failing tests**

```python
async def test_provision_creates_user_profile_trainer_profile_pending_must_change_password(admin_client, db_session) -> None: ...
async def test_response_is_pending_and_never_contains_password(admin_client) -> None: ...
async def test_setup_token_ttl_is_7_days_and_invite_email_is_enqueued_after_commit(admin_client, fake_queue, clock) -> None: ...
async def test_duplicate_email_409_EMAIL_TAKEN_and_duplicate_slug_409_SLUG_TAKEN(admin_client, ...) -> None: ...
async def test_invalid_slug_422(admin_client) -> None: ...   # "Bad Slug", "a", "-x-"
async def test_failure_after_user_insert_rolls_back_all_rows(admin_client, db_count) -> None: ...
async def test_trainer_can_complete_setup_via_password_reset_confirm_and_then_login(client, admin_client) -> None: ...
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_admin_provision_trainer.py` -- Expected: FAIL.

**Step 3: Implement** one transaction: random unusable password hash (`hasher.hash(generate_opaque_token())`, never returned), `status="pending"`, `must_change_password=True`, `trainer_setup` token 7 days (C3; open item O3), audit row, post-commit invite job. Unique violations map to `EMAIL_TAKEN`/`SLUG_TAKEN` by constraint name.

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(admin): provision trainer account with setup token"
```

---

### Task 22: Impersonation (`POST /admin/users/{id}/impersonate`, `POST /auth/impersonation/exit`)

**Files:**
- Create: `app/repositories/impersonation_repo.py`, `app/services/impersonation_service.py`
- Modify: `app/api/v1/admin/routes.py`, `app/api/v1/auth/routes.py`, `app/api/deps.py`, `app/jobs/identity_jobs.py` (cron `expire_impersonation_sessions`)
- Test: `tests/e2e/test_impersonation.py`

**Step 1: Write the failing tests**

```python
async def test_impersonate_returns_token_with_act_claim_and_expiry_at_most_60_min(admin_client, target, codec) -> None: ...
async def test_impersonation_refresh_token_cannot_extend_past_expires_at(admin_client, client, clock) -> None: ...  # D3 / O4: IMPERSONATION_EXPIRED
async def test_impersonating_a_super_admin_is_403_CANNOT_IMPERSONATE_ADMIN(admin_client, other_admin) -> None: ...
async def test_impersonating_while_impersonating_is_403(impersonated_client, target2) -> None: ...
async def test_erased_or_inactive_target_is_409(admin_client, ...) -> None: ...
async def test_every_impersonated_request_has_x_impersonated_by_header_and_an_audit_row(impersonated_client, audit_rows) -> None: ...
async def test_impersonated_principal_gets_403_on_all_admin_routes(impersonated_client) -> None: ...
async def test_impersonated_token_acts_with_target_permissions_only(impersonated_client) -> None: ...
async def test_exit_ends_session_revokes_jti_and_token_is_then_401_IMPERSONATION_EXPIRED(impersonated_client) -> None: ...
async def test_exit_with_a_normal_token_is_403_NOT_IMPERSONATING(user_client) -> None: ...
async def test_expiry_cron_closes_sessions_past_expires_at_idempotently(clock, ...) -> None: ...
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_impersonation.py` -- Expected: FAIL.

**Step 3: Implement** `impersonation_service.start(admin, target_id, reason)`: guards (target not `super_admin`, admin not already `act`, target active), insert session (`expires_at = now + 60 min`, `jti`), access token `sub=target`, `act=admin_id`, TTL min(15 min, remaining)... **Follow O4's recommendation only if `/architect` confirmed it; otherwise implement D3 exactly as written in the spec** (refresh returned but bounded by `expires_at`; after expiry `/auth/refresh` returns `401 IMPERSONATION_EXPIRED`). Write a short comment in the service pointing to O4. `exit` ends the session and sets `ended_at`; `current_principal` already rejects tokens whose session is ended/expired (Task 7).

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(admin): audited, time-boxed impersonation with exit"
```

---

### Task 23: GDPR erasure (`DELETE /admin/users/{id}`)

**Files:**
- Modify: `app/services/user_service.py` (`erase_user`), `app/api/v1/admin/routes.py`, `app/api/v1/admin/schemas.py`, `app/jobs/identity_jobs.py` (`delete_s3_objects`)
- Test: `tests/e2e/test_admin_erasure.py`, `tests/integration/test_erasure_no_plaintext.py`

**Step 1: Write the failing tests**

```python
async def test_erase_anonymises_in_place_and_never_deletes_user_row(admin_client, db_session, victim) -> None:
    # users row still exists: email == f"erased+{id}@invalid", status == "deleted", deleted_at set, names "Deleted User"
async def test_erase_revokes_refresh_families_consumes_one_time_tokens_ends_impersonation_sessions(...) -> None: ...
async def test_erase_writes_user_erasures_row_with_email_hash_and_no_fk_to_user(...) -> None: ...
async def test_erasing_a_parent_erases_owned_dependents_in_same_transaction(...) -> None: ...
async def test_active_commitments_409_via_checker_leaves_everything_untouched(admin_client, busy_checker, ...) -> None: ...
async def test_second_erase_is_409_ALREADY_ERASED_and_unknown_is_404(admin_client, ...) -> None: ...
async def test_body_requires_reason_min_3_chars(admin_client, victim) -> None: ...
async def test_s3_deletion_job_enqueued_after_commit_for_avatar_and_logo_keys(admin_client, fake_queue, ...) -> None: ...
async def test_erased_user_cannot_log_in_or_reset_password(client, ...) -> None: ...
```
```python
# tests/integration/test_erasure_no_plaintext.py
async def test_original_email_appears_nowhere_in_any_table_or_index(db_session, victim) -> None:
    # pg_dump-free approach: for every text/citext/jsonb column in every public table,
    # SELECT count(*) WHERE col::text ILIKE '%<email local part>%'; expect 0 (user_erasures stores only the SHA-256).
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_admin_erasure.py tests/integration/test_erasure_no_plaintext.py` -- Expected: FAIL.

**Step 3: Implement** `erase_user` following the 7 steps in `specs/architect-architecture.md` "Erasure (GDPR)" in order, in one transaction. `DELETE` with a JSON body: use `Body(...)` on the route; document in OpenAPI. Note in the test client helper that httpx needs `client.request("DELETE", url, json=...)`.

**Step 4:** Run the same command -- Expected: PASS.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(admin): GDPR erasure by in-place anonymisation with ledger"
```

---

## M6 Hardening

### Task 24: OpenAPI metadata and contract test

**Files:**
- Modify: `app/main.py`, every `app/api/v1/*/routes.py` (tags, summaries, `responses`, examples)
- Test: `tests/e2e/test_openapi_contract.py`

**Step 1: Write the failing test**

```python
EXPECTED = {  # (method, path) -> must exist; copy the full list from specs/api-designer-spec.md headings
    ("post", "/api/v1/auth/login"), ("post", "/api/v1/auth/refresh"), ("post", "/api/v1/auth/logout"),
    ("post", "/api/v1/auth/register"), ("post", "/api/v1/auth/verify-email"),
    ("post", "/api/v1/auth/verify-email/resend"), ("post", "/api/v1/auth/password-reset/request"),
    ("post", "/api/v1/auth/password-reset/confirm"), ("post", "/api/v1/auth/impersonation/exit"),
    ("get", "/api/v1/me"), ("patch", "/api/v1/me"), ("post", "/api/v1/me/email-change"), ("put", "/api/v1/me/avatar"),
    ("get", "/api/v1/me/availability"), ("put", "/api/v1/me/availability"), ("get", "/api/v1/me/contexts"),
    ("get", "/api/v1/me/players"), ("post", "/api/v1/me/players"),
    ("get", "/api/v1/me/players/{player_id}"), ("patch", "/api/v1/me/players/{player_id}"), ("delete", "/api/v1/me/players/{player_id}"),
    ("post", "/api/v1/me/players/{player_id}/trainers"), ("delete", "/api/v1/me/players/{player_id}/trainers/{trainer_id}"),
    ("put", "/api/v1/me/players/{player_id}/availability"),
    ("get", "/api/v1/me/approvals"), ("post", "/api/v1/me/approvals/{approval_id}/approve"), ("post", "/api/v1/me/approvals/{approval_id}/deny"),
    ("get", "/api/v1/trainer/share-links"), ("post", "/api/v1/trainer/share-links"), ("delete", "/api/v1/trainer/share-links/{link_id}"),
    ("get", "/api/v1/trainer/players/{player_id}/availability"),
    ("get", "/api/v1/trainer/branding"), ("patch", "/api/v1/trainer/branding"), ("put", "/api/v1/trainer/branding/logo"),
    ("get", "/api/v1/public/share-links/{code}"), ("get", "/api/v1/public/trainers/{slug}/branding"),
    ("get", "/api/v1/admin/users"), ("post", "/api/v1/admin/users/trainers"),
    ("get", "/api/v1/admin/users/{user_id}"), ("patch", "/api/v1/admin/users/{user_id}"), ("delete", "/api/v1/admin/users/{user_id}"),
    ("post", "/api/v1/admin/users/{user_id}/deactivate"), ("post", "/api/v1/admin/users/{user_id}/reactivate"),
    ("post", "/api/v1/admin/users/{user_id}/impersonate"),
}

def test_every_spec_endpoint_is_in_openapi_and_nothing_extra(openapi) -> None:
    actual = {(m, p) for p, ops in openapi["paths"].items() for m in ops}
    assert actual == EXPECTED

def test_metadata_security_and_tags(openapi) -> None:
    assert openapi["info"]["title"] == "Trainer Platform API" and openapi["info"]["version"] == "1.0.0"
    assert "BearerAuth" in openapi["components"]["securitySchemes"]
    assert {t["name"] for t in openapi["tags"]} == {"auth", "me", "trainer", "public", "admin"}

def test_public_routes_override_security_empty(openapi) -> None: ...
def test_every_operation_has_summary_and_error_responses_use_ErrorResponse(openapi) -> None: ...
def test_operation_ids_are_tag_prefixed_and_unique(openapi) -> None: ...   # "{tag}_{route.name}"
def test_no_response_schema_contains_forbidden_fields(openapi) -> None:
    # walk components.schemas: none of password_hash, token_hash, ip_hash, logo_key/avatar_key in *Response models
def test_idempotency_key_and_x_trainer_context_headers_documented(openapi) -> None: ...
def test_all_request_schemas_forbid_additional_properties(openapi) -> None: ...
```

**Step 2:** Run `uv run pytest tests/e2e/test_openapi_contract.py` -- Expected: FAIL (missing metadata / extra health route must be `include_in_schema=False`).

**Step 3: Implement** metadata per the spec section "OpenAPI metadata": `generate_unique_id_function`, tag descriptions, `BearerAuth` scheme via `openapi_extra`/custom `openapi()`, `security=[]` on public routes and on the public `/auth/*` routes, `responses={401,403,404,409,422,429: {"model": ErrorResponse}}` declared once per router, `Field(examples=[...])` on key schemas.

**Step 4:** Run `uv run pytest tests/e2e/test_openapi_contract.py` -- Expected: PASS. Also run `uv run python -c "import json,app.main as m; print(json.dumps(m.create_app().openapi())[:80])"` to confirm schema generation works outside tests.

**Step 5: Commit**
```bash
git add app tests
git commit -m "feat(api): OpenAPI metadata, security scheme and contract test"
```

---

### Task 25: Permission matrix and tenant isolation e2e suites

**Files:**
- Create: `tests/e2e/test_permission_matrix.py`, `tests/e2e/test_tenant_isolation.py`, `tests/load/README.md`, `tests/load/locustfile.py` (optional, not run in CI)

**Step 1: Write the tests** (these should already pass if Tasks 7-23 are correct; any failure is a real bug, fix it in the owning service with a regression test)

```python
# Router permission matrix from the spec: parametrise (role, method, path) -> expected status class
MATRIX = [  # role, request, expected
    ("player", ("GET", "/api/v1/trainer/branding"), 403),
    ("child", ("GET", "/api/v1/me/players"), 403),
    ("child", ("GET", "/api/v1/me/approvals"), 403),
    ("child", ("POST", "/api/v1/me/email-change"), 403),
    ("coach", ("GET", "/api/v1/me/contexts"), 403),
    ("trainer", ("GET", "/api/v1/me/availability"), 403),
    ("super_admin", ("GET", "/api/v1/me/availability"), 403),
    ("trainer", ("GET", "/api/v1/admin/users"), 403),
    ("impersonated", ("GET", "/api/v1/admin/users"), 403),
    # ... one row per cell in the spec "Permission matrix" table
]

@pytest.mark.parametrize("role,req,expected", MATRIX)
async def test_matrix_cell(role, req, expected, client_for_role) -> None: ...
```
```python
# tenant isolation: every tenant-scoped route addressed with another tenant's id returns 404 (never 403)
TENANT_ROUTES = [
    ("DELETE", "/api/v1/trainer/share-links/{id}", "foreign_link"),
    ("GET", "/api/v1/trainer/players/{id}/availability", "foreign_player"),
    ("GET", "/api/v1/me/players/{id}", "foreign_dependent"),
    ("POST", "/api/v1/me/approvals/{id}/approve", "foreign_approval"),
    # ... every id-addressed route in me/trainer
]

@pytest.mark.parametrize("method,path,fixture", TENANT_ROUTES)
async def test_cross_tenant_access_is_404(method, path, fixture, request, ...) -> None: ...

async def test_player_with_mismatched_x_trainer_context_is_404_on_every_player_scoped_route(...) -> None: ...
async def test_body_trainer_id_is_ignored_or_rejected_never_trusted(...) -> None: ...
async def test_deactivated_link_removes_tenant_access_immediately(...) -> None: ...
```

**Step 2:** Run `uv run pytest -m integration tests/e2e/test_permission_matrix.py tests/e2e/test_tenant_isolation.py` -- Expected: PASS, or FAIL pointing to a real defect (then `/debugger`, not a test tweak).

**Step 3:** Fix any defect found via a failing regression test in the owning service's test file first.

**Step 4:** Re-run the command -- Expected: PASS (zero cross-tenant reads, NFR-004).

**Step 5: Commit**
```bash
git add tests app
git commit -m "test(e2e): permission matrix and tenant isolation suites"
```

---

### Task 26: Docs sync and Definition of Done

**Files:**
- Modify: `README.md` (run/test instructions: `docker compose up -d`, `uv run alembic upgrade head`, `uv run uvicorn app.main:create_app --factory`), `CHANGELOG.md` (add `[Unreleased]` entry), `specs/MANIFEST.md` only if the spec set changed
- Reference: `.claude/DOD.md`

**Step 1:** Run the full gate:
```bash
docker compose up -d db redis
uv run alembic upgrade head
uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest --cov=app
```
Expected: everything green; coverage report shows services >= 90%.

**Step 2:** Walk `.claude/DOD.md` item by item; record anything not satisfied as a follow-up note in the chat (not in an artifact file).

**Step 3:** Update `README.md` and `CHANGELOG.md`; no duplicated spec text (reference `specs/api-designer-spec.md`).

**Step 4:** Smoke test in a real server: `uv run uvicorn app.main:create_app --factory`, open `/docs`, confirm all 5 tags and 44 operations render, try login with a seeded user.

**Step 5: Commit**
```bash
git add README.md CHANGELOG.md
git commit -m "docs: run instructions and changelog for Epic-01 API"
```

---

## Open items the implementer must NOT decide alone

| Ref | Item | Default in this plan |
|-----|------|----------------------|
| O1/O2 | `PUT` for availability and presign; new `PUT /trainer/branding/logo` path | Implement as in spec |
| O3 | Trainer setup token TTL 24 h (epic) vs 7 d (architecture C3) | 7 d (architecture is binding) |
| O4 | Whether impersonation issues a refresh token at all | Implement D3 as written; revisit if `/architect` confirms the recommended alternative |
| O5 | Client UX for `verification_required` | Out of scope (frontend/product) |
| O6 | `camp_submission_id` depends on Epic-08 | 409 `CAMP_SUBMISSION_UNAVAILABLE` stub with an empty handler registry |
| Task 16 | Dependent age bound (spec says 1-18, requirements text says age < 18) | Confirm with `/architect` before coding the upper bound |
| Arch | Child-login email verification; trainer own availability | Per "Open item" section of `specs/architect-architecture.md`: child inherits verified, trainers excluded |
