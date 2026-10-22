# User Management & Authentication Design

## Problem Statement
The service needs an identity and access foundation that establishes **who the caller is**, **which tenant they belong to**, and **what they may touch**. Key challenges:

- **Multi-role RBAC** with 4 roles (super_admin, trainer, coach, player) and a granular permission matrix where each ❌ cell must return 403 and each "own tenant" cell returns 404.
- **Multi-tenancy**: players may link to many trainers, but every read of player-owned data is scoped per `trainer_id` — there is no aggregated cross-trainer view.
- **Parent/child**: dependent profiles owned by parents, child purchase approvals (48h expiry), child login with reduced permissions, ShareLink blocked for children.
- **ShareLink invitations**: static (players, unlimited, no expiry) and unique (coaches, single-use, 7-day) links with concurrency-safe redemption (20-way parallel redemption of one unique link → exactly 1 success).
- **GDPR erasure**: anonymize PII in place, never `DELETE FROM users`; FKs `ON DELETE RESTRICT` throughout so accidental cascades can't compile.
- **Refresh rotation with reuse detection**: presenting a consumed token revokes the entire family + 401 + audit row.

## Proposed Solution
Layered FastAPI service (route → service → data access) on PostgreSQL + SQLAlchemy asyncio. Authentication via JWT access tokens + rotating refresh tokens, argon2id password hashing, 30-day refresh TTL. Tenant isolation enforced at the service layer via `trainer_player_links`. Email verification is a **hard gate on login** (unverified users cannot authenticate). All transactional emails sent at launch. Skill levels are a **fixed server-defined enum**. Availability windows enable scheduling between players/coaches and trainers. No coach-override notifications (out of MVP).

## Architecture
Layered, request-scoped architecture following the project's invariant: route handler → service → data access. Each layer is thin and single-purpose.

### Layers
1. **Route/handler layer** (FastAPI routers) — parses requests, validates body/query via Pydantic models (`extra="forbid"`), extracts the principal from `deps.py`, delegates to services, returns structured responses. Never contains business logic.
2. **Service layer** — owns all business rules and transaction boundaries. Each service has one cohesive responsibility. Uses repositories/data access internally. All multi-row mutations run in a single DB transaction (SQLAlchemy async session).
3. **Data access / repository layer** — raw SQLAlchemy queries, `SELECT ... FOR UPDATE` locking where needed, migrations via Alembic. No business rules here.

### Core components (from `deps.py`)
- `current_principal` dependency — yields `(user_id, role, trainer_id, act)` per request. Re-reads user status per request so deactivated users are rejected on next request.
- `require_role(...)` — role-based guard (403 otherwise).
- `require_tenant_scope(...)` — tenant-scope guard; `SuperAdminScope` bypass marker.
- `AuditService` — writes audit rows (method, path, target, impersonation target).
- `EmailSender` Protocol — transactional email provider behind an interface; jobs enqueued to ARQ.

### Key design decisions
- **Tenancy**: `trainer_player_links` is the definition of "this player belongs to this tenant." Every player-scoped read takes `trainer_id` and joins through it. No aggregated cross-trainer view in the API.
- **Coach one-trainer**: partial unique index `uq_coach_one_active_trainer ON coach_profiles (user_id) WHERE is_active`. A coach moving trainers deactivates the old row in the same transaction.
- **Impersonation**: `act` claim on the JWT; `impersonation_sessions` row with `jti` for revocation; every `act`-bearing request writes an audit row; `X-Impersonated-By` header on responses; admin routes blocked while impersonating (403).
- **Erasure**: anonymize-in-place. `email = 'erased+<uuid>@invalid'`, randomized `password_hash`, names → "Deleted User", `status = deleted`, `deleted_at` set, all refresh families revoked. FKs `ON DELETE RESTRICT`. Writes `user_erasures` ledger.

## Data Model
SQLAlchemy 2.0 declarative models in `app/models/identity.py`. All PKs UUID v7. All tables carry `created_at`, `updated_at` (`timestamptz`, server default `now()`).

### `users`
`id` (UUID PK) · `email` (citext, UNIQUE, not null) · `password_hash` (text, not null, argon2id) · `role` (enum `user_role`: `super_admin`/`trainer`/`coach`/`player`, not null) · `status` (enum `user_status`: `pending`/`active`/`inactive`/`deleted`, default `pending`) · `is_email_verified` (bool, default false) · `must_change_password` (bool, default false) · `last_login_at` (timestamptz, null) · `deleted_at` (timestamptz, null).
Indexes: `ix_users_role_status`, `ix_users_email` (unique, from citext).

### `profiles` — 1:1 with `users`
`user_id` PK/FK · `first_name` · `last_name` · `phone` · `avatar_key` (S3) · `school` · `timezone` (IANA, default `UTC`).

### `trainer_profiles`
`user_id` PK/FK · `business_name` (not null) · `slug` (UNIQUE, url-safe) · `address` · `website` · `description` · `logo_key` · `brand_primary_color` / `brand_secondary_color` (`CHAR(7)`, hex, CHECK regex `^#[0-9A-Fa-f]{6}$`) · Epic-05 columns (`stripe_account_id`, `subscription_status`, `platform_fee_bps`) added by that epic's migration, not this one.

### `coach_profiles`
`user_id` PK/FK · `trainer_id` FK→`trainer_profiles.user_id` (not null) · `bio` · `credentials` · `certifications` (`JSONB` array) · `is_public` bool · `joined_at`.
> One trainer per active coach — enforced by `CREATE UNIQUE INDEX uq_coach_one_active_trainer ON coach_profiles (user_id) WHERE is_active;`. A coach moving trainers deactivates the old row inside the same transaction.

### `player_profiles`
`id` PK · `owner_user_id` FK→`users` (the account holder: the adult themself, or the parent) · `login_user_id` FK→`users` nullable (set when a child has their own login) · `display_name` · `birth_date` (date, not null) · `gender` enum · `skill_level` enum nullable (set by trainer) · `school` · `jersey_number` · `is_dependent` bool · `emergency_contact_name` / `_phone` · `token_spend_requires_approval` bool default **true**.
Constraints: `CHECK (NOT is_dependent OR owner_user_id IS NOT NULL)`; `CHECK (age_years(birth_date) BETWEEN 1 AND 18)` applied only when `is_dependent`. Index `ix_player_profiles_owner`.

### `trainer_player_links` — the tenancy join
`id` PK · `trainer_id` FK · `player_profile_id` FK · `source` enum (`share_link`/`event_registration`/`coach_invite`/`camp_conversion`/`admin`) · `share_link_id` FK nullable · `linked_at` · `status` enum (`active`/`inactive`).
`UNIQUE (trainer_id, player_profile_id)`. Index `ix_tpl_trainer_status`.
**This table is the definition of "this player belongs to this tenant"** — every other epic joins through it.

### `share_links`
`id` PK · `code` (text, `UNIQUE`, 22-char URL-safe, generated with `secrets.token_urlsafe`) · `trainer_id` FK · `created_by_user_id` FK · `kind` enum (`player_static`/`coach_unique`) · `target_email` nullable (coach links) · `expires_at` nullable · `max_uses` nullable · `use_count` int default 0 · `is_active` bool.
Rules encoded as CHECKs: `player_static` ⇒ `expires_at IS NULL AND max_uses IS NULL`; `coach_unique` ⇒ `expires_at IS NOT NULL AND max_uses = 1`.

### `share_link_events` (analytics, feeds Epic-03/06)
`id` · `share_link_id` FK · `kind` enum (`opened`/`registered`) · `occurred_at` · `user_agent_hash` · `ip_hash` (salted SHA-256, never raw IP).

### `availability_windows`
`id` PK · `subject_type` enum (`player`/`coach`) · `subject_id` UUID · `weekday` smallint 0–6 · `start_time` / `end_time` (`time`) · `is_available` bool.
`CHECK (start_time < end_time)`. Index `ix_availability_subject (subject_type, subject_id, weekday)`. Overlap prevention via `EXCLUDE USING gist` on `(subject_type WITH =, subject_id WITH =, weekday WITH =, timerange WITH &&)`.

### `purchase_approvals`
`id` PK · `player_profile_id` FK · `parent_user_id` FK · `trainer_id` FK · `subject_type` enum (`event_rsvp`/`content`/`token_purchase`) · `subject_id` UUID · `amount_cents` nullable · `token_amount` nullable · `status` enum (`pending`/`approved`/`denied`/`expired`) · `requested_at` · `responded_at` nullable · `expires_at` (= requested_at + 48 h) · `parent_note`.
Index `ix_purchase_approvals_pending (status, expires_at)` — drives the expiry sweeper job.

### `impersonation_sessions`
`id` PK · `admin_user_id` FK · `target_user_id` FK · `started_at` · `ended_at` nullable · `expires_at` (= started_at + 60 min) · `reason` nullable · `jti` (the issued token's id, for revocation).

### `user_erasures` (GDPR ledger)
`id` PK · `original_user_id` UUID (no FK — the row survives) · `original_email_hash` (SHA-256) · `erased_by_user_id` FK · `reason` · `erased_at` · `legal_retention_until` date.

### `refresh_tokens`
`id` PK · `user_id` FK · `family_id` UUID · `token_hash` (SHA-256, `UNIQUE`) · `expires_at` · `consumed_at` nullable · `revoked_at` nullable · `user_agent_hash` · `ip_hash`.
Index `ix_refresh_family (family_id) WHERE revoked_at IS NULL`.

## API Design
All endpoints under `/api/v1`. Request/response schemas defined with Pydantic models (`extra="forbid"`). Error responses follow the project contract: 404 for not-found (also cross-tenant isolation), 409 for conflict, 403 for forbidden, 422 for validation errors, 401 for auth failures.

### Authentication & Session
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/login` | POST | Email/password login with rate limiting |
| `/auth/refresh` | POST | Rotating refresh token with reuse detection |
| `/auth/logout` | POST | Revoke refresh family |
| `/auth/password-reset/request` | POST | Password reset request (no user enumeration) |
| `/auth/password-reset/confirm` | POST | Password reset confirmation (single-use token) |
| `/auth/verify-email` | POST | Email verification |
| `/auth/verify-email/resend` | POST | Verification email resend (rate limited) |
| `/auth/register` | POST | Public registration via ShareLink or camp submission |
| `/auth/impersonation/exit` | POST | End impersonation session |

### Current User
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/me` | GET/PATCH | Own profile (names, phone, school, timezone, avatar); role/email/status not patchable |
| `/me/avatar` | PUT | Presigned S3 URL + key |
| `/me/availability` | GET/PUT | Weekly player availability; replaces full set |
| `/me/contexts` | GET | Trainer contexts player can switch between |
| `/me/players` | GET/POST | List + create dependent profiles |
| `/me/players/{id}` | GET/PATCH/DELETE | Own dependents only |
| `/me/players/{id}/trainers` | POST | Add child↔trainer link |
| `/me/players/{id}/trainers/{trainer_id}` | DELETE | Remove link (status=inactive) |
| `/me/players/{id}/availability` | PUT | Parent can set availability for dependent |
| `/me/approvals` | GET | Pending purchase approvals (parent) |
| `/me/approvals/{id}/approve` | POST | Approve (idempotent with Idempotency-Key) |
| `/me/approvals/{id}/deny` | POST | Deny |

### Trainer Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/trainer/share-links` | GET/POST | List + issue ShareLinks (static player, unique coach) |
| `/trainer/share-links/{id}` | DELETE | Deactivate ShareLink |
| `/trainer/players/{id}/availability` | GET | Read player availability (tenant-scoped) |
| `/trainer/branding` | GET/PATCH | Portal branding (logo + colors) |

### Public Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/public/share-links/{code}` | GET | ShareLink preview (unauthenticated) |
| `/public/trainers/{slug}/branding` | GET | Trainer branding (unauthenticated, cached 5 min) |

### Super Admin Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/users` | GET | Cursor-paged (limit/forward) with filters: `role`, `status`, `trainer`, `query` |
| `/admin/users/trainers` | POST | Provision trainer account |
| `/admin/users/{id}` | GET/PATCH | User management |
| `/admin/users/{id}/deactivate` | POST | Soft-delete (status=inactive) |
| `/admin/users/{id}/reactivate` | POST | Restore active (cannot restore deleted) |
| `/admin/users/{id}` | DELETE | GDPR erasure |
| `/admin/users/{id}/impersonate` | POST | Returns scoped token pair (act=admin claim) |

## Error Handling
Errors raised at the service layer as domain exceptions (`NotFoundError`, `ConflictError`, `ForbiddenError`, `UnauthorizedError`, `ValidationError`) and converted to structured JSON responses at the route boundary with the project's error contract:
- `400` — malformed request
- `401` — auth failure (INVALID_CREDENTIALS, REFRESH_REUSE_DETECTED, IMPERSONATION_EXPIRED)
- `403` — forbidden (each ❌ cell in the permission matrix → `403 CHILD_CANNOT_REDEEM`, `CANNOT_IMPERSONATE_ADMIN`, `LINK_EMAIL_MISMATCH`)
- `404` — not found AND cross-tenant isolation (each "own tenant" cell → `404` for another tenant's id; ShareLink unknown/inactive/expired/exhausted → `404 SHARE_LINK_INVALID` with a single code — no oracle)
- `409` — conflict (EMAIL_TAKEN, SLUG_TAKEN, ACCOUNT_EXISTS, DUPLICATE_PLAYER, WRONG_LINK_KIND, APPROVAL_NOT_PENDING, ACTIVE_COMMITMENTS, ALREADY_ERASED, USER_ERASED)
- `422` — validation (Pydantic `extra="forbid"` on every request model; INVALID_AGE, OVERLAPPING_WINDOWS)

Constant-time login failure: a missing user still runs a dummy argon2id hash so the failure path timing is identical. Email verification gate: **hard gate on login** — unverified users cannot authenticate.

## Testing Strategy
- **Unit tests**: `AvailabilityService.check_conflict` at minute boundaries (window 16:00–18:00 vs. events 15:59, 16:00, 18:00, 18:01); service layer logic; password policy (zxcvbn ≥ 3).
- **Integration tests**: repositories against PostgreSQL test database; transaction isolation (register → one transaction, failure leaves zero rows); ShareLink concurrency — 20 parallel redemptions of one `coach_unique` link → exactly 1 success, 19 × 404; approval approve concurrency — 10-way parallel → idempotent under Idempotency-Key.
- **E2E tests**: all §8 endpoints through FastAPI TestClient / httpx; permission matrix — one test per ❌ cell and per "own tenant" cell; cross-tenant isolation returns 404 on every tenant-scoped route; GDPR test asserts no plaintext original email survives in any table or index; refresh-reuse detection revokes the family.
- **Load tests** (`tests/load/`): p95 login < 300ms at 50 RPS; 200 concurrent ShareLink redemptions, 0 lost writes; GET /admin/users over 10k users < 500ms p95 with `EXPLAIN ANALYZE` showing index scan.

## Security Considerations
- **Authentication**: argon2id (`time_cost=3, memory_cost=64 MiB, parallelism=4`, ≥ 100ms verification); constant-time failure path; no user enumeration (`401 INVALID_CREDENTIALS` every failure); password policy ≥ 12 chars, zxcvbn ≥ 3.
- **Tokens**: JWT access + rotating refresh; consuming a token stamps `consumed_at`, issues successor in same family; presenting a consumed token revokes the whole family + `401 REFRESH_REUSE_DETECTED` + audit row; refresh TTL 30 days; impersonation TTL ≤ 60 min with `act` claim; `X-Impersonated-By` header on impersonated responses.
- **Authorization**: `require_role` / `require_tenant_scope` enforced at service layer; `SuperAdminScope` bypass marker; impersonated principals blocked from `/admin/*` (403).
- **Data protection**: no raw IP storage (salted SHA-256 hash); user_agent_hash only; email_change keeps old email active until confirmation; avatar two-phase upload validates content-type and size (≤ 5 MB, `image/jpeg|png|webp`); branding logo two-phase (≤ 2 MB, `image/png|svg+xml|webp`), SVG sanitised (script/foreignObject stripped) — the one place this epic sanitises input; PKs UUID v7, no sequential IDs exposed.

## Open Questions
All Q-01.x resolved:
- Q-01.05 → hard gate on login (verified email required to authenticate)
- Q-01.07 → refresh TTL 30 days
- Q-01.01 → fixed server-defined skill-level enum
- Q-01.02 → exact age (year + month + day)
- Q-01.04 → all listed transactional emails at launch
- Q-01.06 → no coach-override notification (out of MVP)
