# 00 — Backend Conventions (applies to every epic)

Every epic in this directory assumes what follows. Epics do **not** repeat it; they reference it.

---

## 1. Stack

| Concern | Choice | Notes |
|:---|:---|:---|
| Language | Python 3.12 | `X \| None` unions and modern generics allowed (see GOLDEN-PRINCIPLES.md) |
| Dependency / env | **uv** | `uv sync`, `uv run <cmd>`; lockfile committed |
| Web framework | **FastAPI** | ASGI, uvicorn (dev) / gunicorn + uvicorn workers (prod) |
| Validation | **Pydantic v2** | Request/response schemas only — ORM models never leave the API layer |
| ORM | **SQLAlchemy 2.0** async (`asyncpg`) | Typed `Mapped[...]` declarative models |
| Migrations | **Alembic** | One migration per schema change; no `create_all` outside tests |
| Database | **PostgreSQL 16** | Sole source of truth for domain state |
| Cache / locks / rate limit | **Redis 7** | Also the broker for background jobs |
| Background jobs | **ARQ** (Redis-backed) | Celery acceptable if the team already runs it; job contracts below are broker-agnostic |
| Outbound HTTP | **httpx** async | Timeouts mandatory; no bare `requests` |
| Auth | **JWT** access + opaque rotating refresh token | See §4 |
| Password hashing | **argon2id** (`argon2-cffi`) | |
| Payments | **Stripe** Python SDK | Epic-05 |
| Object storage | S3-compatible via boto3, presigned URLs | Profile photos, CSV exports |
| Email | Provider-agnostic `EmailSender` Protocol + one adapter | Domain code depends on the Protocol, not the vendor |
| Lint / format | **ruff** | `ruff check`, `ruff format` |
| Types | **mypy** | Strict on `app/`; `disallow_untyped_defs = true` |
| Tests | **pytest** + `pytest-asyncio` + `httpx.AsyncClient` | |
| Observability | `structlog` JSON logs + OpenTelemetry traces | Correlation id per request |

**Explicit non-goals**: no server-rendered templates, no Jinja pages, no frontend build step, no client
assets. The deliverable is a JSON API plus its background workers.

---

## 2. Layering (enforced)

```
app/
  api/v1/<domain>/routes.py     # FastAPI routers: HTTP only — parse, authorize, delegate, serialize
  api/v1/<domain>/schemas.py    # Pydantic request/response models
  api/deps.py                   # DI: db session, current_user, tenant scope, pagination
  services/<domain>_service.py  # business rules, transactions, orchestration — no HTTP, no SQL strings
  repositories/<domain>_repo.py # SQLAlchemy queries — no business rules
  models/<domain>.py            # SQLAlchemy declarative models
  jobs/<domain>_jobs.py         # background tasks
  core/                         # config, security, errors, logging, pagination, clock
  db/                           # session factory, base, alembic env
tests/
  unit/  integration/  e2e/
```

Rules in force for every epic:

- A router function contains **no business logic** and **no query construction**.
- A service **never** imports from `app.api`, never sees a `Request`, never returns a Pydantic response model.
- A repository returns ORM entities or scalars and does not commit — the service owns the transaction boundary.
- Cross-domain calls go service → service, never service → another domain's repository.
- Anything a background worker must also do lives in a service, so route and job share one implementation.

---

## 3. Multi-tenancy (the most important invariant)

Every trainer is a tenant. Isolation between trainers is a **security** property, not a filter.

- Every tenant-scoped table carries `trainer_id` (FK, `NOT NULL`, indexed).
- Repositories for tenant-scoped tables take `trainer_id` as a **required** parameter. A bare
  `get(id)` must not exist for these tables.
- The tenant is derived from the authenticated principal in `deps.py`, never from a client-supplied body field.
- Every endpoint reachable by Trainer / Coach / Player has a test proving another trainer's row
  returns **404** — not 403, because a 403 confirms the row exists.
- Super Admin bypass is explicit per-endpoint (`SuperAdminScope` marker), never a global escape hatch.

---

## 4. AuthN / AuthZ

**Tokens**

- Access: JWT, TTL 15 min, claims `sub`, `role`, `trainer_id`, `act` (impersonator id, optional), `jti`.
- Refresh: opaque 256-bit random, stored SHA-256 hashed, TTL 30 days, **rotated on every use**.
  Reuse of a consumed refresh token revokes the whole family (theft detection).
- Logout revokes the refresh family; short access TTL makes an access denylist unnecessary.

**Roles** — `super_admin`, `trainer`, `coach`, `player`. A parent is a `player` who has dependents.
Exactly one role per user.

**Authorization** is a dependency, never an `if` inside a handler:

```python
@router.post("/events", dependencies=[Depends(require_role(Role.TRAINER))])
```

Object-level checks ("does this trainer own this event?") live in the service and raise
`NotFoundError` on cross-tenant misses.

**Impersonation** (Epic-01 / Epic-07) — the issued token keeps `sub` = impersonated user and adds
`act` = admin id. Every request carrying `act` is audit-logged. Sessions cap at 60 min and may never
target another `super_admin`.

**Rate limiting** — Redis fixed-window, keyed by (route class, IP, principal):

| Route class | Limit |
|:---|:---|
| `POST /auth/login` | 10/min per IP **and** 5/min per email |
| `POST /auth/password-reset` | 5/hour per email |
| Public form submit (Epic-08) | 20/hour per IP |
| Everything else | 300/min per principal |

Exceeding returns `429` with `Retry-After`.

---

## 5. API conventions

- Base path `/api/v1`. Plural resource names; multi-word paths use hyphens (`/share-links`).
- `GET` list/detail, `POST` create, `PATCH` partial update (no `PUT` on resources), `DELETE` soft-delete.
- Non-CRUD transitions are action sub-resources: `POST /events/{id}/cancel`,
  `POST /rsvps/{id}/cancel`, `POST /coach-assignments/{id}/confirm`.
- **UUIDv7** primary keys everywhere — time-sortable, non-enumerable.
- Timestamps are `timestamptz`, stored UTC, serialized ISO-8601 with `Z`.
- Money is integer **cents** (`amount_cents: int`) plus `currency` (`"usd"` for MVP). Never float.
- Tokens (the virtual currency) are integers; fractional tokens do not exist.

**Pagination** — cursor-based, one envelope everywhere:

```json
{ "items": [], "next_cursor": "eyJpZCI6...", "has_more": true }
```

`?limit=` defaults to 50, max 200. Offset pagination is permitted only on admin CSV exports.

**Filtering / sorting** — explicit query params declared in a Pydantic `...FilterParams` model per
endpoint. No generic query-language passthrough.

**Error contract** — one shape from every endpoint, emitted by a single exception handler:

```json
{ "error": { "code": "EVENT_FULL", "message": "Event has no remaining capacity", "details": { "capacity": 20 } } }
```

Domain exceptions map to status codes centrally:

| Exception | Status |
|:---|:---|
| Pydantic `ValidationError` | 422 |
| `NotFoundError` | 404 |
| `PermissionDeniedError` | 403 |
| `ConflictError` (duplicate, capacity, illegal state) | 409 |
| `PaymentRequiredError` | 402 |
| `RateLimitedError` | 429 |
| `ExternalServiceError` | 502 |

Handlers never assemble error bodies by hand.

**Error code registry** — every code added by the admin user-management rules (Epic-01
US-01.07, 01.12, 01.13). The cited story is authoritative for when each is raised; this table
is the index.

| Code | Status | Exception | Story |
|:---|:---|:---|:---|
| `CANNOT_TARGET_SELF` | 403 | `PermissionDeniedError` | US-01.07, 01.12, 01.13 |
| `CANNOT_MODIFY_ADMIN` | 403 | `PermissionDeniedError` | US-01.12, 01.13 |
| `CANNOT_IMPERSONATE_ADMIN` | 403 | `PermissionDeniedError` | US-01.07 |
| `USER_PENDING` | 409 | `ConflictError` | US-01.12 |
| `USER_ERASED` | 409 | `ConflictError` | US-01.12 |
| `ALREADY_ERASED` | 409 | `ConflictError` | US-01.13 |
| `IMPERSONATION_EXPIRED` | 401 | none (rejected at token validation, no domain exception in the table above) | US-01.07 |

**Idempotency** — every endpoint that moves money or consumes capacity accepts an
`Idempotency-Key` header. Keys live in
`idempotency_keys(key, principal_id, endpoint, request_hash, response_status, response_body, created_at)`
with `UNIQUE (key, principal_id)`. A replay returns the stored response instead of re-executing.

---

## 6. Concurrency rules

Capacity, balances, and counters are contested. They are handled one of three ways — never as a
read-then-write in Python:

1. **Atomic conditional UPDATE** (preferred):
   `UPDATE events SET rsvp_count = rsvp_count + 1 WHERE id = :id AND rsvp_count < capacity RETURNING rsvp_count`.
   Zero rows affected ⇒ `ConflictError("EVENT_FULL")`.
2. **`SELECT ... FOR UPDATE`** on the aggregate root when several rows move together
   (token debit + RSVP insert), inside one transaction.
3. **Unique constraint as arbiter** for "at most one" facts — `UNIQUE (event_id, player_id)` for
   RSVPs, `UNIQUE (form_id, lower(email))` for form submissions. Catch `IntegrityError`, translate
   to `ConflictError`.

Every such rule ships with a concurrency test (§8, e2e tier).

---

## 7. Background jobs & webhooks

- Jobs are **idempotent** and **retryable**; at-least-once delivery is assumed. A job takes ids, not
  objects, and re-reads state.
- Retry policy: exponential backoff, 5 attempts, then dead-letter with a structured log record.
- Each epic lists its jobs as: name · trigger · payload · effect · idempotency key.
- Inbound webhooks (Stripe): signature verified **before** parsing; the raw event is persisted to
  `webhook_events(provider, external_id UNIQUE, type, payload, received_at, processed_at, status)`;
  the endpoint returns `200` once persisted and processes asynchronously. A duplicate `external_id`
  is acknowledged, not reprocessed.
- Outbound email is always a job, never inline in a request handler.

---

## 8. Testing (pytest)

Three tiers, all under `uv run pytest`:

| Tier | Location | Scope | Dependencies |
|:---|:---|:---|:---|
| unit | `tests/unit/` | one service or one pure rule; repositories faked via Protocol | none |
| integration | `tests/integration/` | service + real Postgres, real transactions | testcontainers or disposable `test_` DB |
| e2e | `tests/e2e/` | `httpx.AsyncClient` against the ASGI app, auth included | Postgres + Redis + Stripe test mode / respx |

Standing requirements for every epic:

- Each user story's acceptance criteria map to at least one named test.
- Each business rule that can reject a request has a **negative** test asserting error code + status.
- Each tenant-scoped endpoint has a cross-tenant isolation test asserting `404`.
- Each role-gated endpoint has a test per disallowed role asserting `403`.
- Each capacity/balance rule has a concurrency test (`asyncio.gather` over N competing calls,
  asserting exactly the expected number succeed).
- Time-dependent rules (24h refund window, 48h approval expiry, midnight attendance lock) are tested
  on both sides of the boundary with an injected `Clock` Protocol. Domain code never calls
  `datetime.now()` directly.
- Coverage floor: 85% on `app/services/` and `app/api/`.

---

## 9. Security baseline (OWASP-aligned, per AGENTS.md)

- All input validated at the boundary by Pydantic; no `dict[str, Any]` reaches a service.
- SQL exclusively through SQLAlchemy constructs — no f-string SQL anywhere.
- Free text is stored raw and escaped by consumers. Any field ever rendered as HTML is sanitised on
  write (`bleach`); such fields are named explicitly in the epic that owns them.
- Secrets come from the environment (`pydantic-settings`) only. `.env` is never read or committed.
- PII (email, phone, DOB, notes) never reaches logs; `structlog` processors redact by key name.
- Every state-changing admin action writes an audit row (Epic-07).
- Uploads: content-type allowlist, size cap, virus-scan hook point, random storage keys.
- `uv run pip-audit` runs in CI.

---

## 10. Definition of Done for an epic

In addition to [`../../.claude/DOD.md`](../../.claude/DOD.md) **Full** tier:

- [ ] Every US in the epic has endpoints implemented, schema'd, and present in the OpenAPI output
- [ ] Alembic migrations apply cleanly forward and have a tested `downgrade`
- [ ] `uv run pytest` green; coverage floor met
- [ ] `uv run ruff check . && uv run ruff format --check .` clean
- [ ] `uv run mypy app tests` clean
- [ ] Isolation, role, and concurrency tests present per §8
- [ ] Background jobs registered and covered by at least one integration test
- [ ] Performance targets verified by a load script (`locust` or `k6`) against seeded data
- [ ] `specs/api-designer-spec.md` and `specs/MANIFEST.md` updated per AGENTS.md
