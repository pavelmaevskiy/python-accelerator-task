# Architecture

**System:** a single multi-tenant FastAPI JSON API with its ARQ background workers. One service, one
PostgreSQL 16 database, Redis 7 for rate limits, caching and the job queue.
**Style:** request-scoped layers: route → service → repository → model, with layer-first packages.
**Tenancy:** each trainer is a tenant. Isolation is a security property, and a cross-tenant miss
returns **404**.
**Normative baseline:** `Task/Epics/00_Backend_Conventions.md` (stack, error contract, pagination,
concurrency, jobs, testing). This file records only decisions made *on top of* that baseline.

---

## System-wide structure

### Package layout (layer-first, per conventions §2)

```
app/
  core/          config · security (jwt, argon2, tokens) · errors · logging · pagination · clock
                 · idempotency · rate_limit · storage (S3 Protocol) · email (EmailSender Protocol)
  db/            session factory · declarative Base · alembic/
  models/        <domain>.py           e.g. identity.py, admin.py
  repositories/  <domain>_repo.py      tenant-scoped repos REQUIRE trainer_id, no bare get(id)
  services/      <name>_service.py     business rules + transaction boundary
  api/
    deps.py      principal, guards, db session, pagination, rate-limit deps
    v1/<area>/   routes.py + schemas.py   (auth, me, trainer, coach, public, admin, ...)
  jobs/          <domain>_jobs.py + cron registry (worker settings)
tests/  unit/ integration/ e2e/ load/
```

**Decision:** we use a layer-first layout rather than the skill's default feature-first layout
(`<pkg>/<feature>/`). The conventions doc mandates it, and every epic spec assumes these paths.

### Dependency rules

| From | May import | Must not import |
|------|-----------|-----------------|
| `api/v1/*` | `services`, `api/deps`, `core` | `repositories`, `models` (ORM models never leave API layer as responses) |
| `services` | own + other domains' **services**, own repositories, `core` | `api`, other domains' repositories, `fastapi` |
| `repositories` | `models`, `db`, `core` | `services`, `api` |
| `jobs` | `services`, `core` | `repositories` directly |

Cross-epic seams are `typing.Protocol`s declared by the **consumer** (see Approval subject handlers).

### Transaction boundary

- The request gets one `AsyncSession` from `deps.get_session`. The **service** opens `async with session.begin()`.
  Repositories never commit.
- Job enqueueing happens **after** commit. We use a post-commit hook on the unit of work, so a
  rolled-back transaction never sends an email.
- Audit rows that must survive a failed request, such as an impersonated request or refresh-reuse
  detection, are written in a **separate short transaction**.

---

### [TASK-001] Epic-01 Identity, Tenancy & Auth (2026-10-21)

**Source design:** `tasks/TASK-001/brainstorm-design.md` (data model, endpoints, error codes).
This section records module placement, the corrections we made to that design, and the cross-cutting
mechanisms.

#### Module placement

| Concern | Module |
|---------|--------|
| Models | `app/models/identity.py` (users, profiles, trainer/coach/player profiles, links, share links, availability, approvals, impersonation, erasures, refresh + one-time tokens); `app/models/admin.py` (audit_log, feature_flags); `app/core/idempotency` model in `app/models/core.py` |
| Repositories | `user_repo`, `profile_repo`, `tenancy_repo` (trainer_player_links, coach memberships), `share_link_repo`, `availability_repo`, `approval_repo`, `token_repo` (refresh + one-time), `impersonation_repo`, `audit_repo`, `feature_flag_repo` |
| Services | `auth_service` (login, refresh, logout, verify, reset), `registration_service` (ShareLink + invite completion), `user_service` (me, admin user mgmt, deactivate, **erasure**), `player_service` (dependents, child↔trainer links, contexts), `sharelink_service`, `availability_service`, `approval_service`, `branding_service`, `impersonation_service`, `audit_service`, `feature_flag_service` |
| Security primitives | `app/core/security.py`: `PasswordHasher` (argon2id + dummy hash), `TokenCodec` (JWT encode/decode, `kid` rotation), `generate_opaque_token()` + `hash_token()` (SHA-256) |
| Rate limiting | `app/core/rate_limit.py` (Redis fixed window, Lua INCR+EXPIRE) exposed as `deps.rate_limit("login")` |
| Uploads | `app/core/storage.py` `ObjectStorage` Protocol (presign, head, get, put, delete); `app/core/svg_sanitizer.py` |
| Email | `app/core/email.py` `EmailSender` Protocol + one adapter; called **only** from `app/jobs/identity_jobs.py` |
| Routers | `api/v1/auth`, `api/v1/me`, `api/v1/trainer`, `api/v1/public`, `api/v1/admin` |

`registration_service` is split from `auth_service`. Registration is the largest multi-row
transaction in the epic and depends on ShareLink, tenancy and profile services. Keeping it out of
`auth_service` keeps that service focused on credentials and tokens, and keeps both modules under
about 300 lines.

#### Corrections to the brainstorm data model (binding)

| # | Issue in design | Decision |
|---|-----------------|----------|
| C1 | `coach_profiles.user_id` is the PK, so the partial unique index `ON (user_id) WHERE is_active` can never matter, and `is_active` does not exist. | Split into `coach_profiles` (user_id PK: bio, credentials, certifications, is_public) and **`coach_memberships`** (`id`, `coach_user_id` FK, `trainer_id` FK, `is_active`, `joined_at`, `left_at`) with `uq_coach_one_active_trainer ON coach_memberships (coach_user_id) WHERE is_active`. A move deactivates and inserts in one transaction. |
| C2 | `CHECK (age_years(birth_date) BETWEEN 1 AND 18)` depends on the current date, so it is not immutable and PostgreSQL rejects it. | Age is validated in `player_service` using the injected `Clock` (exact y/m/d, per Q-01.02), which raises `422 INVALID_AGE`. The DB keeps only `CHECK (birth_date > '1900-01-01')`. |
| C3 | No storage exists for the email-verification, password-reset, trainer-setup or email-change tokens. | Add **`one_time_tokens`** (`id`, `user_id` FK, `kind` enum `email_verify`/`password_reset`/`trainer_setup`/`email_change`, `token_hash` UNIQUE, `payload` JSONB (e.g. `new_email`), `expires_at`, `consumed_at`). Consumption is an atomic `UPDATE ... SET consumed_at=now() WHERE token_hash=:h AND consumed_at IS NULL AND expires_at > now() RETURNING`. TTLs are verify 48 h, reset 1 h, setup 7 d and email_change 24 h. Issuing a new token of a kind invalidates earlier unconsumed tokens of that kind. |
| C4 | `availability_windows.subject_id` is polymorphic with no FK, which contradicts the "FK RESTRICT everywhere" rule for erasure. PostgreSQL also has no built-in `time` range type. | Use two nullable FKs, `player_profile_id` and `coach_user_id`, with `CHECK (num_nonnulls(...) = 1)`. The migration runs `CREATE EXTENSION btree_gist` and `CREATE TYPE timerange AS RANGE (subtype = time)`, and adds a stored generated column `span timerange GENERATED ALWAYS AS (timerange(start_time, end_time, '[)'))`. Add two `ExcludeConstraint`s (one per subject FK, `WHERE ... IS NOT NULL`) on `(subject = , weekday =, span &&)`. Half-open ranges let 16:00–18:00 and 18:00–20:00 coexist. |
| C5 | `user_status=pending` overlaps with `is_email_verified`. | `pending` means the account has no usable credentials yet (a trainer invite that has not been completed). Login requires `status='active' AND is_email_verified`. |
| C6 | The brainstorm uses `ForbiddenError` and `ValidationError`, which do not match the conventions and clash with Pydantic. | Use the conventions names `PermissionDeniedError`(403), `NotFoundError`, `ConflictError`, `RateLimitedError`, and add `AuthenticationError`(401) and `DomainValidationError`(422) in `app/core/errors.py`. |
| C7 | "`require_role` enforced at service layer" contradicts conventions §4. | Role guards are FastAPI **dependencies** on the router. Object and tenant checks live in **services** and raise `NotFoundError`. `require_tenant_scope` only *derives* `trainer_id` from the principal. |
| C8 | `share_links` concurrency: `FOR UPDATE` on a static link would serialise 200 concurrent registrations. | `coach_unique` links use an atomic conditional `UPDATE share_links SET use_count=use_count+1 WHERE id=:id AND is_active AND expires_at>now() AND use_count<max_uses RETURNING id`, and 0 rows means `404 SHARE_LINK_INVALID`. `player_static` links use an unconditional `use_count=use_count+1` without a row lock. Duplicate-email races are settled by `users.email` UNIQUE, which becomes `409 EMAIL_TAKEN`. |

The cross-cutting tables `audit_log`, `feature_flags` and `idempotency_keys` (conventions §5) ship in
the Epic-01 migration set. Epic-07 owns their admin surface.

#### Entity relationships

```
users 1─1 profiles
users 1─1 trainer_profiles ──┐
users 1─1 coach_profiles     │
users 1─N coach_memberships N─1 trainer_profiles     (≤1 active per coach)
users(owner) 1─N player_profiles ─1 users(login, nullable, child login)
trainer_profiles 1─N trainer_player_links N─1 player_profiles   ← TENANCY JOIN
trainer_profiles 1─N share_links 1─N share_link_events
player_profiles / users(coach) 1─N availability_windows
player_profiles 1─N purchase_approvals N─1 users(parent), N─1 trainer_profiles
users 1─N refresh_tokens (grouped by family_id) · users 1─N one_time_tokens
users(admin) 1─N impersonation_sessions N─1 users(target)
user_erasures: no FK to original user (ledger survives)
All FKs ON DELETE RESTRICT. All PKs UUIDv7 (app-generated, `uuid6`/`uuid_utils`).
```

#### Principal resolution (`api/deps.py`)

```
Authorization: Bearer <jwt>
  → TokenCodec.decode (HS256, kid lookup, exp/nbf, typ=access)
  → user_repo.get_auth_state(sub)  -- 1 PK lookup: status, role, is_email_verified
       status != active  → 401
  → if act: impersonation_repo.get_active(jti) else 401 IMPERSONATION_EXPIRED
  → Principal(user_id, role, trainer_id, act, is_child)
```

- `trainer_id` claim: for a trainer it is their own id; for a coach it is the active membership's
  trainer. For a player it is the **selected context**, which `/me/contexts` provides and which must
  appear in a validated `X-Trainer-Context` header. It is checked against active
  `trainer_player_links` in the service and never trusted from the request body.
- `is_child` = the user is the `login_user_id` of a dependent `player_profile`. Child restrictions,
  such as `403 CHILD_CANNOT_REDEEM` or blocked dependent creation, are dependencies or service checks
  driven by this flag.
- An impersonated request (`act` present) causes these actions:
  - an audit row is written in its own transaction;
  - the response gets an `X-Impersonated-By` header;
  - the `/admin/*` router dependency raises `403`.
- We re-read user state on every request. This costs one indexed PK query and keeps the
  "deactivated user rejected on the next request" guarantee without an access-token denylist. The
  `GET /me` p95 target of 100 ms leaves ample margin.

#### Authentication decisions

- **JWT:** HS256 with the key from settings and a `kid` header, so keys can rotate (current plus
  previous accepted). Access TTL is 15 min. Claims are `sub`, `role`, `trainer_id`, `act`, `jti` and
  `typ`. If a second verifier ever appears, we move to EdDSA/RS256; until then a symmetric key is
  simpler and adequate.
- **Refresh:** an opaque 256-bit token, stored as a SHA-256 hash, with a 30-day TTL (Q-01.07) and
  rotation as specified. Families live in Postgres, the sole source of truth. We do not keep a Redis
  copy: the refresh path is low-RPS and the row lock gives correctness.
- **Email verification is a hard gate (Q-01.05):** login verifies the password first, then rejects
  an unverified user with `403 EMAIL_NOT_VERIFIED` and issues no tokens.
  - This reveals verification status only to someone who already holds the password, so it is not an
    enumeration oracle.
  - Every other failure returns `401 INVALID_CREDENTIALS` on the constant-time path.
  - `POST /auth/verify-email/resend` takes an email, always returns `202`, and is rate-limited per
    email and per IP.
- **Implicit verification** applies when the flow itself proves mailbox control:
  - completing a `trainer_setup` token;
  - registering through a `coach_unique` link whose `target_email` matches;
  - completing a password reset.
- **Child logins:** a child login is created by a verified parent, and its `is_email_verified` is
  inherited as true. There is no independent minor account, and the parent is the contact of record.
  *This needs product confirmation (see the open item below).*
- **Argon2 capacity:** at about 100 ms of CPU and 64 MiB per verification, 50 RPS of logins needs
  about 5 cores and about 3.2 GB peak hash memory. We run hashing in `anyio.to_thread.run_sync`
  (argon2-cffi releases the GIL) so the event loop never blocks. Login rate limits (10/min/IP,
  5/min/email) cap abuse.

#### Approval subject handlers (Protocol seam to Epic-02/05)

`approval_service` owns the `purchase_approvals` aggregate: the lifecycle, the 48 h expiry, the
idempotent approve and deny, and the notifications. It declares:

```python
class ApprovalSubjectHandler(Protocol):
    subject_type: ApprovalSubjectType
    async def on_approved(self, session: AsyncSession, approval: ApprovalView) -> None: ...
    async def on_denied_or_expired(self, session: AsyncSession, approval: ApprovalView) -> None: ...
```

Epic-02 (`event_rsvp`) and Epic-05 (`content`, `token_purchase`) register their handlers at startup.
In Epic-01 the registry ships empty. An approval whose subject type has no registered handler fails
with `409 APPROVAL_SUBJECT_UNAVAILABLE`, which unit tests exercise with a fake. Approve runs in a
single transaction: `SELECT ... FOR UPDATE` on the approval, then the handler, then the status
update, all keyed by `Idempotency-Key`.

#### Erasure (GDPR)

`user_service.erase_user` runs in one transaction:

1. Lock the user.
2. Refuse with `409 ACTIVE_COMMITMENTS` while future paid commitments exist. This check goes through
   a Protocol that Epic-02/05 implement and that returns "none" until they land.
3. Anonymise `users`, `profiles` and `player_profiles` PII for dependents the user owns. Also
   anonymise `trainer_profiles` contact fields, and set `coach_profiles.bio` and `credentials` to
   null.
4. Revoke all refresh families, consume all `one_time_tokens`, and end impersonation sessions.
5. Insert the `user_erasures` row with the email hash.
6. Write the audit row.
7. Enqueue an S3 deletion of the avatar and logo keys, as an idempotent job.

The `citext` UNIQUE on email keeps the `erased+<uuid>@invalid` placeholder unique. The integration
test greps every table and index for the original email.

#### Uploads (avatar, logo)

Uploads are two-phase:

1. `PUT /me/avatar` returns a presigned PUT to a `quarantine/<uuid>` key, with a content-length
   condition.
2. A confirm call (the `PATCH /me` or `/trainer/branding` body field `avatar_key` or `logo_key`)
   makes the service do the following:
   1. `HEAD` the object;
   2. check the size and **magic bytes** (never only the client content-type);
   3. for an SVG, fetch it, parse it with `defusedxml`, run an allowlist sanitiser (stripping
      `script`, `foreignObject`, `on*` attributes and external `href`s), and re-upload it;
   4. copy the object to its final random key;
   5. delete the quarantine object.

The `ObjectStorage` Protocol keeps boto3 out of the services. boto3 calls run in a thread, or we use
`aioboto3`.

#### Security

| Control | Mechanism |
|---------|-----------|
| AuthN | argon2id (`t=3, m=64MiB, p=4`), constant-time dummy hash, uniform 401, password policy ≥ 12 chars plus zxcvbn ≥ 3 |
| AuthZ | role dependencies on routers; tenant/object checks in services (404 on cross-tenant); `SuperAdminScope` per endpoint; `/admin/*` blocked under `act` |
| Input | Pydantic `extra="forbid"` everywhere; `EmailStr`; hex-colour regex; IANA timezone validated against `zoneinfo.available_timezones()` |
| Rate limiting | Redis fixed window: login 10/min/IP and 5/min/email; reset 5/h/email; resend 3/h/email; public share-link preview 60/min/IP; default 300/min/principal; `429` + `Retry-After` |
| Enumeration | Uniform `404 SHARE_LINK_INVALID`; reset and resend always return `202`; share-link codes have 128 bits (`token_urlsafe(16)` gives 22 chars) |
| Secrets | `pydantic-settings`: JWT keys, IP-hash salt, S3 and email credentials |
| Privacy | Salted SHA-256 of IP and user agent; structlog redaction of `email`, `phone`, `birth_date`, `password*` and `token*` |
| Audit | `audit_service.record(...)` for every admin mutation, impersonation start, exit and request, refresh reuse, and erasure |
| Data | No sequential ids (UUIDv7); no hard deletes of users; `ON DELETE RESTRICT` |

#### Scalability

| Concern | Decision |
|---------|----------|
| Indexes | Indexes in the design, plus the following: `ix_tpl_player (player_profile_id, status)` for `/me/contexts`; `ix_one_time_tokens_user_kind (user_id, kind) WHERE consumed_at IS NULL`; `ix_users_created_id (created_at, id)` for the admin cursor; `pg_trgm` GIN on `users.email` and on `profiles` names for the admin `query` filter (10k-user p95 < 500 ms); `ix_impersonation_jti` UNIQUE |
| Caching | `/public/trainers/{slug}/branding` uses Redis read-through (TTL 300 s, key deleted on PATCH) plus `Cache-Control: public, max-age=300`. Nothing else is cached in Epic-01 |
| Async work | All email goes through ARQ jobs (§12 of the epic) enqueued after commit. Cron jobs (`expire_purchase_approvals`, `expire_impersonation_sessions`, `purge_expired_tokens`) use set-based `UPDATE ... WHERE status=... AND expires_at < now()`, so they are idempotent |
| Horizontal scale | API nodes are stateless (JWT plus a per-request DB read). All shared state is in Postgres and Redis. Argon2 is the CPU bottleneck, so scale on CPU. The DB pool is sized per worker (for example 10), and pgbouncer runs in transaction mode only if needed |
| Contention | Coach unique link: conditional UPDATE. Static link: lock-free increment. Approvals: row lock plus idempotency key. Refresh: row lock on token hash |

#### Open item (needs product confirmation)

- **Child login and the email gate:** the default is that a child's login is implicitly verified
  through the parent. If product requires children to verify their own email, add a `child_invite`
  kind to `one_time_tokens`. The schema does not change.
- **Trainer availability:** the permission matrix shows "Set own availability ✅" for trainers, but
  the design's subjects cover only players and coaches. For now we keep trainers out of
  `availability_windows`, because Epic-02 owns trainer scheduling. A third nullable FK can be added
  if needed.
