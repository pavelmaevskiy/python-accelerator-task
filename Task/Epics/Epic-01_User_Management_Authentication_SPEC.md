# Epic-01 (Python Backend): User Management & Authentication

> Stack, layering, error contract, pagination, tenancy and testing rules: [00_Backend_Conventions.md](00_Backend_Conventions.md).

**Priority**: P0 — foundation, blocks every other epic
**Complexity**: High (multi-role RBAC, multi-tenancy, impersonation, GDPR erasure)
**User stories**: 14

---

## 1. Description

The identity and access foundation of the service: authentication, refresh-token lifecycle,
role-based authorization, tenant isolation, the profile graph (trainer / coach / player / parent-child),
the ShareLink invitation system, availability data, admin impersonation, and GDPR-compliant erasure.

Scope boundary: this epic owns **who the caller is**, **which tenant they belong to**,
and **what they may touch**. Every other epic consumes that answer through `deps.py` and never
re-derives it.

## 2. Backend outcomes

- A `current_principal` dependency any router can declare, yielding `(user_id, role, trainer_id, act)`.
- A `require_role(...)` / `require_tenant_scope(...)` pair every downstream epic reuses.
- Password, session, and token handling that passes a security review without amendment.
- An identity schema whose `trainer_id` column makes tenant leakage a constraint violation, not a code review finding.
- Erasure that anonymises PII while keeping referential integrity for attendance and financial history.

**Success metrics (measurable server-side)**

- p95 `POST /auth/login` < 300 ms; p95 authenticated GET on profile endpoints < 200 ms.
- 0 cross-tenant reads in the isolation test suite (hard gate).
- `GET /admin/users?limit=50` over 10 000 users < 500 ms p95.
- Impersonation coverage: 100% of `act`-bearing requests produce an audit row.

---

## 3. In scope (MVP)

### Authentication & session
- [ ] `POST /auth/login` — email + password, argon2id verification, constant-time failure path
- [ ] `POST /auth/refresh` — rotating refresh token with reuse detection
- [ ] `POST /auth/logout` — revoke refresh family
- [ ] `POST /auth/password-reset/request` and `POST /auth/password-reset/confirm` (1 h TTL, single-use)
- [ ] `POST /auth/verify-email` + `POST /auth/verify-email/resend` (24 h TTL)
- [ ] Forced password change on first login for admin-provisioned trainers
- [ ] Rate limiting per Conventions §4

### Authorization
- [ ] 4 roles, one per user; `require_role` dependency
- [ ] Tenant scope resolution + `SuperAdminScope` bypass marker
- [ ] Permission matrix (§6) enforced at the service layer, asserted by tests

### User management (Super Admin)
- [ ] `GET /admin/users` — cursor-paginated directory with filters (role, status, trainer, query)
- [ ] `POST /admin/users/trainers` — provision a trainer account + invite email job
- [ ] `PATCH /admin/users/{id}` — edit any account
- [ ] `POST /admin/users/{id}/deactivate` / `.../reactivate` — soft delete, history preserved
- [ ] `DELETE /admin/users/{id}` — GDPR erasure: PII anonymised, rows retained
- [ ] `POST /admin/users/{id}/impersonate` and `POST /auth/impersonation/exit`
- [ ] Impersonation audit records

### Player / parent
- [ ] Registration via ShareLink (`POST /auth/register?share_code=`)
- [ ] Camp-submission conversion registration (pre-filled from Epic-08 submission id)
- [ ] Multi-trainer association with **isolated per-trainer context**
- [ ] Parent trains as a player (parent account is itself a player profile)
- [ ] Dependent (child) profile CRUD
- [ ] Parent manages child↔trainer associations
- [ ] Child purchase-approval request/approve/deny workflow (48 h expiry)
- [ ] Child login with reduced permission set
- [ ] ShareLink blocked for child accounts → parent notification instead
- [ ] Availability windows (weekly, per player)

### Coach
- [ ] Coach bound to exactly one trainer (enforced by partial unique index)
- [ ] Coach availability windows ("My Times")
- [ ] Availability conflict evaluation exposed to Epic-02
- [ ] Public profile fields (bio, credentials, certifications, visibility)

### Trainer
- [ ] ShareLink issuing: static (players, unlimited, no expiry) and unique (coaches, single-use, 7 d)
- [ ] Read player availability for scheduling (consumed by Epic-02)
- [ ] Manage own organisation's users
- [ ] Portal branding: logo upload (presigned S3) + colour palette

---

## 4. Out of scope (Post-MVP)

Open Gym / League Instructor and Referee roles · social login · 2FA · per-user permission
customisation · messaging hub · Build-a-Bag · player feedback tool · custom role editor · advanced
branding · bulk import/export · public API keys for third parties.

> Simple portal branding (logo + colours) **is** in MVP — US-01.14.

---

## 5. Dependencies

**Required before**: none — this is the root epic.

**Blocks**: Epic-02, Epic-03, Epic-04, Epic-05, Epic-06, Epic-07, Epic-08.

**Infrastructure**: PostgreSQL 16 · Redis 7 (rate limits, refresh-family revocation, job queue) ·
S3-compatible storage (avatars, logos) · transactional email provider behind the `EmailSender` Protocol.

**Python packages**: `fastapi`, `pydantic[email]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`,
`argon2-cffi`, `pyjwt`, `redis`, `arq`, `boto3`, `python-multipart`, `structlog`.

---

## 6. Roles & permission matrix

| Capability | super_admin | trainer | coach | player (adult) | player (child) |
|:---|:---:|:---:|:---:|:---:|:---:|
| Read own profile | ✅ | ✅ | ✅ | ✅ | ✅ |
| Update own profile | ✅ | ✅ | ✅ | ✅ | limited fields |
| Create trainer account | ✅ | ❌ | ❌ | ❌ | ❌ |
| Read any user | ✅ | own tenant only | assigned events only (Epic-03) | ❌ | ❌ |
| Issue player ShareLink | ✅ | ✅ | ✅ (Epic-03) | ❌ | ❌ |
| Issue coach ShareLink | ✅ | ✅ | ❌ | ❌ | ❌ |
| Consume ShareLink | ❌ | ❌ | ✅ (unique) | ✅ | ❌ → parent notified |
| Create dependent profile | ✅ | ❌ | ❌ | ✅ | ❌ |
| Approve child purchase | ✅ | ❌ | ❌ | ✅ (own dependents) | ❌ |
| Set own availability | ❌ | ✅ | ✅ | ✅ | ✅ |
| Read player availability | ✅ | own tenant | ❌ | own | own |
| Impersonate | ✅ (not other super_admins) | ❌ | ❌ | ❌ | ❌ |
| Deactivate / erase user | ✅ | ❌ | ❌ | ❌ | ❌ |
| Update portal branding | ✅ | own tenant | ❌ | ❌ | ❌ |

Each ❌ in this table is a test asserting `403`; each "own tenant" is a test asserting `404` for
another tenant's id.

---

## 7. Domain model

SQLAlchemy 2.0 declarative, `app/models/identity.py` unless noted. All PKs `UUID` (v7).
All tables carry `created_at`, `updated_at` (`timestamptz`, server default `now()`).

### `users`
| Column | Type | Constraints |
|:---|:---|:---|
| `id` | UUID | PK |
| `email` | `citext` | `UNIQUE`, not null |
| `password_hash` | text | not null (argon2id) |
| `role` | enum `user_role` | not null — `super_admin`/`trainer`/`coach`/`player` |
| `status` | enum `user_status` | not null, default `pending` — `pending`/`active`/`inactive`/`deleted` |
| `is_email_verified` | bool | default false |
| `must_change_password` | bool | default false |
| `last_login_at` | timestamptz | null |
| `deleted_at` | timestamptz | null |

Indexes: `ix_users_role_status`, `ix_users_email` (unique, from citext).

### `profiles` — 1:1 with `users`
`user_id` PK/FK · `first_name` · `last_name` · `phone` · `avatar_key` (S3) · `school` · `timezone`
(IANA, default `UTC`).

### `trainer_profiles`
`user_id` PK/FK · `business_name` (not null) · `slug` (`UNIQUE`, url-safe) · `address` · `website` ·
`description` · `logo_key` · `brand_primary_color` / `brand_secondary_color` (`CHAR(7)`, hex, CHECK
regex) · Epic-05 columns (`stripe_account_id`, `subscription_status`, `platform_fee_bps`) added by
that epic's migration, not this one.

### `coach_profiles`
`user_id` PK/FK · `trainer_id` FK→`trainer_profiles.user_id` (not null) · `bio` · `credentials` ·
`certifications` (`JSONB` array) · `is_public` bool · `joined_at`.

> **One trainer per active coach** — enforced by
> `CREATE UNIQUE INDEX uq_coach_one_active_trainer ON coach_profiles (user_id) WHERE is_active;`
> A coach moving trainers deactivates the old row inside the same transaction.

### `player_profiles`
`id` PK · `owner_user_id` FK→`users` (the account holder: the adult themself, or the parent) ·
`login_user_id` FK→`users` nullable (set when a child has their own login) · `display_name` ·
`birth_date` (date, not null) · `gender` enum · `skill_level` enum nullable (set by trainer) ·
`school` · `jersey_number` · `is_dependent` bool · `emergency_contact_name` / `_phone` ·
`token_spend_requires_approval` bool default **true**.

Constraints: `CHECK (NOT is_dependent OR owner_user_id IS NOT NULL)`;
`CHECK (age_years(birth_date) BETWEEN 1 AND 18)` applied only when `is_dependent`.
Index `ix_player_profiles_owner`.

### `trainer_player_links` — the tenancy join
`id` PK · `trainer_id` FK · `player_profile_id` FK · `source` enum
(`share_link`/`event_registration`/`coach_invite`/`camp_conversion`/`admin`) ·
`share_link_id` FK nullable · `linked_at` · `status` enum (`active`/`inactive`).

`UNIQUE (trainer_id, player_profile_id)`. Index `ix_tpl_trainer_status`.
**This table is the definition of "this player belongs to this tenant"** — every other epic joins
through it.

### `share_links`
`id` PK · `code` (text, `UNIQUE`, 22-char URL-safe, generated with `secrets.token_urlsafe`) ·
`trainer_id` FK · `created_by_user_id` FK · `kind` enum (`player_static`/`coach_unique`) ·
`target_email` nullable (coach links) · `expires_at` nullable · `max_uses` nullable ·
`use_count` int default 0 · `is_active` bool.

Rules encoded as CHECKs: `player_static` ⇒ `expires_at IS NULL AND max_uses IS NULL`;
`coach_unique` ⇒ `expires_at IS NOT NULL AND max_uses = 1`.

### `share_link_events` (analytics, feeds Epic-03/06)
`id` · `share_link_id` FK · `kind` enum (`opened`/`registered`) · `occurred_at` ·
`user_agent_hash` · `ip_hash` (salted SHA-256, never raw IP).

### `availability_windows`
`id` PK · `subject_type` enum (`player`/`coach`) · `subject_id` UUID · `weekday` smallint 0–6 ·
`start_time` / `end_time` (`time`) · `is_available` bool.

`CHECK (start_time < end_time)`. Index `ix_availability_subject (subject_type, subject_id, weekday)`.
Overlap prevention via `EXCLUDE USING gist` on `(subject_type WITH =, subject_id WITH =, weekday WITH =, timerange WITH &&)`.

### `purchase_approvals`
`id` PK · `player_profile_id` FK · `parent_user_id` FK · `trainer_id` FK · `subject_type` enum
(`event_rsvp`/`content`/`token_purchase`) · `subject_id` UUID · `amount_cents` nullable ·
`token_amount` nullable · `status` enum (`pending`/`approved`/`denied`/`expired`) ·
`requested_at` · `responded_at` nullable · `expires_at` (= requested_at + 48 h) · `parent_note`.

Index `ix_purchase_approvals_pending (status, expires_at)` — drives the expiry sweeper job.

### `impersonation_sessions`
`id` PK · `admin_user_id` FK · `target_user_id` FK · `started_at` · `ended_at` nullable ·
`expires_at` (= started_at + 60 min) · `reason` nullable · `jti` (the issued token's id, for revocation).

### `user_erasures` (GDPR ledger)
`id` PK · `original_user_id` UUID (no FK — the row survives) · `original_email_hash` (SHA-256) ·
`erased_by_user_id` FK · `reason` · `erased_at` · `legal_retention_until` date.

### `refresh_tokens`
`id` PK · `user_id` FK · `family_id` UUID · `token_hash` (SHA-256, `UNIQUE`) · `expires_at` ·
`consumed_at` nullable · `revoked_at` nullable · `user_agent_hash` · `ip_hash`.
Index `ix_refresh_family (family_id) WHERE revoked_at IS NULL`.

---

## 8. API surface

| Method | Path | Role | Notes |
|:---|:---|:---|:---|
| POST | `/api/v1/auth/register` | public | `?share_code=` or `?camp_submission_id=` |
| POST | `/api/v1/auth/login` | public | rate limited |
| POST | `/api/v1/auth/refresh` | public | rotating |
| POST | `/api/v1/auth/logout` | any | revokes family |
| POST | `/api/v1/auth/password-reset/request` | public | always `202`, no user enumeration |
| POST | `/api/v1/auth/password-reset/confirm` | public | single-use token |
| POST | `/api/v1/auth/verify-email` | public | |
| POST | `/api/v1/auth/verify-email/resend` | any | rate limited |
| POST | `/api/v1/auth/impersonation/exit` | impersonated | ends session |
| GET/PATCH | `/api/v1/me` | any | own profile |
| PUT | `/api/v1/me/avatar` | any | presigned upload → confirm |
| GET/PUT | `/api/v1/me/availability` | player, coach | full weekly replace |
| GET | `/api/v1/me/contexts` | player | the trainer contexts this account can switch between |
| GET/POST | `/api/v1/me/players` | player | dependents (list / create) |
| GET/PATCH/DELETE | `/api/v1/me/players/{id}` | player | own dependents only |
| POST | `/api/v1/me/players/{id}/trainers` | player | add child↔trainer link |
| DELETE | `/api/v1/me/players/{id}/trainers/{trainer_id}` | player | remove link |
| GET | `/api/v1/me/approvals` | player (parent) | pending requests |
| POST | `/api/v1/me/approvals/{id}/approve` \| `/deny` | player (parent) | idempotent |
| GET/POST | `/api/v1/trainer/share-links` | trainer | list / issue |
| DELETE | `/api/v1/trainer/share-links/{id}` | trainer | deactivate |
| GET | `/api/v1/trainer/players/{id}/availability` | trainer | tenant-scoped |
| GET/PATCH | `/api/v1/trainer/branding` | trainer | logo + colours |
| GET | `/api/v1/public/share-links/{code}` | public | preview: trainer name, kind, validity |
| GET | `/api/v1/public/trainers/{slug}/branding` | public | for the consuming client |
| GET | `/api/v1/admin/users` | super_admin | filters + cursor |
| POST | `/api/v1/admin/users/trainers` | super_admin | provision |
| GET/PATCH | `/api/v1/admin/users/{id}` | super_admin | |
| POST | `/api/v1/admin/users/{id}/deactivate` \| `/reactivate` | super_admin | |
| DELETE | `/api/v1/admin/users/{id}` | super_admin | GDPR erasure |
| POST | `/api/v1/admin/users/{id}/impersonate` | super_admin | returns scoped token pair |

---

## 9. User stories → backend tasks

### US-01.01 — Super Admin provisions a trainer account

`POST /api/v1/admin/users/trainers`

```json
{ "email": "bob@hoops.io", "first_name": "Bob", "last_name": "Ray",
  "phone": "+15551234567", "business_name": "Hoops Academy", "slug": "hoops-academy" }
```

**Acceptance criteria**
- [ ] `201` returns `{id, email, business_name, slug, status: "pending"}`; no password in the response
- [ ] Creates `users` + `profiles` + `trainer_profiles` in **one transaction**; a failure on any leaves zero rows
- [ ] `must_change_password = true`; a random 32-byte password is set and never returned by the API
- [ ] Enqueues `send_trainer_invite` with a single-use setup token (24 h TTL)
- [ ] Duplicate email ⇒ `409 EMAIL_TAKEN` (from the unique constraint, not a pre-check race)
- [ ] Duplicate slug ⇒ `409 SLUG_TAKEN`
- [ ] Non-`super_admin` caller ⇒ `403`
- [ ] Writes an audit row `trainer.created` (Epic-07 table; this epic writes through `AuditService`)

### US-01.02 — Player registers via ShareLink

`POST /api/v1/auth/register?share_code=AbC123...`

**Acceptance criteria**
- [ ] Unknown / inactive / expired / exhausted code ⇒ `404 SHARE_LINK_INVALID` (one code for all four — no oracle)
- [ ] New email ⇒ creates `users(role=player)`, `profiles`, `player_profiles`, `trainer_player_links(source=share_link)`; returns `201` + token pair
- [ ] Existing **authenticated** caller with the same code ⇒ `200`, creates only the missing link, no second account
- [ ] Existing email, unauthenticated ⇒ `409 ACCOUNT_EXISTS` with `details.action = "login_then_redeem"`
- [ ] Parent with dependents supplies `player_profile_ids[]`; only listed profiles get a link; empty list ⇒ `422`
- [ ] A profile id not owned by the caller ⇒ `404` (isolation, not 403)
- [ ] `coach_unique` link redeemed by a non-coach ⇒ `409 WRONG_LINK_KIND`
- [ ] `use_count` incremented atomically; a `coach_unique` link at `max_uses` ⇒ `404 SHARE_LINK_INVALID`
- [ ] Records `share_link_events(kind=registered)`
- [ ] Enqueues `send_welcome_email`
- [ ] **Concurrency**: 20 parallel redemptions of one `coach_unique` link ⇒ exactly 1 success, 19 × `404`

### US-01.03 — Parent creates a dependent profile

`POST /api/v1/me/players`

**Acceptance criteria**
- [ ] Required: `display_name`, `birth_date`, `gender`; `422` otherwise
- [ ] `is_dependent = true` requires computed age 1–18 ⇒ else `422 INVALID_AGE`
- [ ] `trainer_ids[]` optional; each id must already be linked to the caller ⇒ else `404`
- [ ] Creates one `trainer_player_links` row per supplied trainer, `source=share_link` inherited from the parent's own link where known, else `admin`
- [ ] Same name + birth_date already under this owner ⇒ `409 DUPLICATE_PLAYER` unless `?allow_duplicate=true`
- [ ] `201` returns the profile with its links
- [ ] A parent's own player profile is created implicitly at registration — a parent can train themselves without a dependent row

### US-01.04 — Parent manages child↔trainer associations

`POST /api/v1/me/players/{id}/trainers` · `DELETE /api/v1/me/players/{id}/trainers/{trainer_id}`

**Acceptance criteria**
- [ ] Add: `201`; already linked ⇒ `200` (idempotent), never a duplicate row
- [ ] Remove: sets `status = inactive`, never hard-deletes — attendance and payment history must survive
- [ ] Removal with a future paid RSVP ⇒ `409 ACTIVE_COMMITMENTS` listing blocking rsvp ids in `details`
- [ ] Player profile not owned by caller ⇒ `404`
- [ ] `GET /api/v1/me/players/{id}` returns links with `linked_at` and `source`

### US-01.05 — Child purchase requires parent approval

`POST /api/v1/me/approvals/{id}/approve` · `.../deny`

**Acceptance criteria**
- [ ] Any purchase initiated by a `login_user_id` on a dependent profile creates a `purchase_approvals` row in `pending` and returns `202` with the approval id — never charges
- [ ] USD purchases **always** require approval
- [ ] Token spend requires approval when `player_profiles.token_spend_requires_approval` is true (default); `PATCH /api/v1/me/players/{id}` toggles it per child
- [ ] Approve ⇒ delegates to the owning service (Epic-02 RSVP / Epic-05 payment) and transitions to `approved` in the same transaction as the effect
- [ ] Deny ⇒ `denied` + `parent_note`; enqueues `notify_child_decision`
- [ ] Approving an `expired` or already-resolved row ⇒ `409 APPROVAL_NOT_PENDING`
- [ ] Approving another parent's row ⇒ `404`
- [ ] `expire_purchase_approvals` job flips `pending` → `expired` at `expires_at`
- [ ] Approve is idempotent under `Idempotency-Key`: two identical calls ⇒ one charge

### US-01.06 — Child login with constraints

**Acceptance criteria**
- [ ] A dependent gets a login only when the parent sets one; credentials live on a separate `users` row referenced by `player_profiles.login_user_id`
- [ ] A child token's `trainer_id` claim is restricted to trainers linked to that one player profile
- [ ] Child may: read own events/content, request RSVP (→ approval), set own availability, read own profile
- [ ] Child may **not**: redeem a ShareLink (`403 CHILD_CANNOT_REDEEM` + `notify_parent_share_link_blocked` job), create dependents, manage payment methods, complete a purchase
- [ ] Every child-forbidden endpoint has a test asserting `403`

### US-01.07 — Super Admin impersonates a user

`POST /api/v1/admin/users/{id}/impersonate`

**Acceptance criteria**
- [ ] `200` returns a token pair whose access token carries `sub = target`, `act = admin`, TTL ≤ 60 min
- [ ] Target equals the caller ⇒ `403 CANNOT_TARGET_SELF` (checked first; the caller is always a `super_admin`, so this check must precede the role check or it could never fire)
- [ ] Target is *another* `super_admin` ⇒ `403 CANNOT_IMPERSONATE_ADMIN`
- [ ] Opens an `impersonation_sessions` row; `POST /auth/impersonation/exit` closes it and revokes the `jti`
- [ ] Session past `expires_at` ⇒ token rejected `401 IMPERSONATION_EXPIRED`
- [ ] Every request with `act` present writes an audit row including method, path, and target
- [ ] Responses to impersonated requests include header `X-Impersonated-By: <admin_id>` so clients can display a banner
- [ ] An impersonated principal may not call any `/admin/*` route ⇒ `403`

### US-01.08 — Trainer invites a coach

`POST /api/v1/trainer/share-links` with `{"kind": "coach_unique", "target_email": "..."}`

**Acceptance criteria**
- [ ] `201` returns `{code, kind, expires_at, max_uses: 1}`; `expires_at = now + 7d`
- [ ] Redemption by a user whose email ≠ `target_email` ⇒ `403 LINK_EMAIL_MISMATCH`
- [ ] Redemption by a user already an active coach elsewhere ⇒ `409 COACH_ALREADY_ASSIGNED` naming the current trainer in `details`
- [ ] Successful redemption creates `coach_profiles` and marks the link exhausted in one transaction
- [ ] A trainer issuing a link for another trainer's tenant is impossible — `trainer_id` comes from the token

### US-01.09 — Player sets availability

`PUT /api/v1/me/availability`

**Acceptance criteria**
- [ ] Body is the **full weekly set**; the endpoint replaces, it does not merge (delete + insert in one transaction)
- [ ] Overlapping windows on the same weekday ⇒ `422 OVERLAPPING_WINDOWS` (also guarded by the GiST exclusion constraint)
- [ ] `start_time >= end_time` ⇒ `422`
- [ ] Times are stored as local wall-clock plus the profile's IANA `timezone`; comparison against an event uses the **event's** timezone
- [ ] A parent may `PUT /api/v1/me/players/{id}/availability` for a dependent; another owner's profile ⇒ `404`
- [ ] Availability is readable by linked trainers only (US-01.14 tenancy), never by other players

### US-01.10 — Coach sets My Times

Same contract as US-01.09 with `subject_type = coach`.

**Acceptance criteria**
- [ ] Coach availability is readable by the coach's own trainer and by super_admin; any other caller ⇒ `404`
- [ ] Epic-02 calls `AvailabilityService.check_conflict(subject, start, end)` — a pure function over windows, unit-tested on boundary minutes (window 16:00–18:00 vs. events 15:59, 16:00, 18:00, 18:01)

### US-01.11 — User edits own profile

`PATCH /api/v1/me` · `PUT /api/v1/me/avatar`

**Acceptance criteria**
- [ ] Editable: names, phone, school, timezone, avatar; role/status/email are **not** patchable here
- [ ] Email change goes through `POST /api/v1/me/email-change` → re-verification; the old email stays active until confirmation
- [ ] Avatar upload is two-phase: `PUT /me/avatar` returns a presigned S3 URL + `avatar_key`; a follow-up `POST /me/avatar/confirm` validates content-type and size (≤ 5 MB, `image/jpeg|png|webp`) before persisting the key
- [ ] Unknown fields in the body ⇒ `422` (Pydantic `extra="forbid"` on every request model)

### US-01.12 — Super Admin deactivates a user

`POST /api/v1/admin/users/{id}/deactivate`

**Acceptance criteria**
- [ ] `status = inactive`; all refresh families revoked; existing access tokens rejected on next `deps` check (status is re-read per request)
- [ ] Historical rows (RSVPs, attendance, transactions) untouched
- [ ] Deactivating a trainer cascades to *hiding* their tenant from players but never deletes data
- [ ] `.../reactivate` restores `active`; a `deleted` user cannot be reactivated ⇒ `409 USER_ERASED`; a `pending` user (never activated, so nothing to restore) ⇒ `409 USER_PENDING`
- [ ] A super_admin deactivating their own account ⇒ `403 CANNOT_TARGET_SELF`; deactivating another `super_admin` ⇒ `403 CANNOT_MODIFY_ADMIN`
- [ ] Audit row on both transitions

### US-01.13 — Super Admin erases a user (GDPR)

`DELETE /api/v1/admin/users/{id}`

**Acceptance criteria**
- [ ] Anonymises in place: `email = 'erased+<uuid>@invalid'`, `password_hash` randomised, `profiles` names → `"Deleted User"`, phone/school/avatar nulled, `player_profiles.display_name` → `"Deleted Player"` for profiles whose `owner_user_id` is the erased user (profiles that only reference them via `login_user_id` are not modified)
- [ ] `status = deleted`, `deleted_at` set, all refresh families revoked
- [ ] FKs are **never** cascaded — attendance, RSVP, and transaction rows keep pointing at the anonymised user
- [ ] Writes `user_erasures` with the hashed original email and `legal_retention_until`
- [ ] Dependents of an erased parent are erased in the same transaction
- [ ] Erasing a user transitions every `pending` `purchase_approvals` row to `expired` in the same transaction when its `player_profile_id` references a profile with `owner_user_id` = the erased user **or** `login_user_id` = the erased user (`responded_at` stays null, no notification job is enqueued). This covers an adult's own profile, a parent's dependents (erased with the parent), and a child's own login user erased directly. Erasing only a child's login user anonymises that user and leaves the dependent profile and its owner untouched (`login_user_id` keeps pointing at the anonymised row; no cascade). A later approve/deny ⇒ `409 APPROVAL_NOT_PENDING`
- [ ] A parent may set a new login for a dependent whose `login_user_id` references a `deleted` user; this replaces the pointer (US-01.06)
- [ ] A super_admin erasing their own account ⇒ `403 CANNOT_TARGET_SELF`; erasing another `super_admin` ⇒ `403 CANNOT_MODIFY_ADMIN`
- [ ] The operation is irreversible; a second `DELETE` ⇒ `409 ALREADY_ERASED`
- [ ] Test asserts no plaintext of the original email survives in any table or index

### US-01.14 — Trainer configures portal branding

`GET/PATCH /api/v1/trainer/branding`

**Acceptance criteria**
- [ ] Colours validated against `^#[0-9A-Fa-f]{6}$` ⇒ else `422`
- [ ] Logo upload uses the same two-phase presigned flow as avatars; ≤ 2 MB, `image/png|svg+xml|webp`
- [ ] SVG uploads are sanitised on write (script/`foreignObject` stripped) — the one place this epic sanitises input
- [ ] `GET /api/v1/public/trainers/{slug}/branding` is unauthenticated, cached 5 min, and exposes only logo URL + colours + business name
- [ ] A trainer patching another tenant's branding is impossible — `trainer_id` comes from the token

---

## 10. Business rules (service layer)

**Authentication**
- Argon2id parameters: `time_cost=3, memory_cost=64 MiB, parallelism=4`; tuned so verification ≥ 100 ms.
- Login failure is constant-time: a missing user still runs a dummy hash verification.
- Login never reveals whether the email exists — `401 INVALID_CREDENTIALS` in every failure case.
- Unverified email may log in but receives `email_verified: false`; endpoints listed as verification-gated return `403 EMAIL_NOT_VERIFIED` (Q-01.05 governs which — see §14).
- Password policy: ≥ 12 chars, checked against a breached-password list (`zxcvbn` score ≥ 3).

**Tokens**
- Refresh rotation: consuming token *T* issues *T'* in the same family and stamps `consumed_at` on *T*.
- Presenting a `consumed` token ⇒ revoke the entire family + `401 REFRESH_REUSE_DETECTED` + audit row.

**Tenancy**
- Trainer accounts are created **only** by super_admin; there is no trainer self-registration endpoint.
- A player may link to many trainers. Every read of player-owned data takes a `trainer_id` and returns
  only that context's rows — there is no aggregated cross-trainer view anywhere in the API.
- A coach is active under exactly one trainer (partial unique index, §7).

**Parent / child**
- Every profile with `is_dependent` and age < 18 is owned by a parent account; there is no independent minor account.
- Contact details of record live on the parent's `profiles` row.
- Approval requests expire at 48 h (or immediately when the owner or login user of the requesting player profile is erased) and cannot be resurrected.

**Impersonation**
- Never targets `super_admin`; capped at 60 min; every request audited; admin routes blocked while impersonating.

**Erasure**
- Anonymise-in-place, never `DELETE FROM users`. Foreign keys are `ON DELETE RESTRICT` throughout this schema
  precisely so an accidental cascade cannot compile.

---

## 11. Backend flows

**Registration via ShareLink**
```
POST /auth/register?share_code=X
  ├─ resolve+lock share_link (SELECT ... FOR UPDATE)
  ├─ validate: active, not expired, use_count < max_uses, kind matches caller
  ├─ INSERT users → profiles → player_profiles → trainer_player_links
  ├─ UPDATE share_links SET use_count = use_count + 1
  ├─ INSERT share_link_events(kind=registered)
  ├─ COMMIT
  └─ enqueue send_welcome_email, enqueue send_verification_email
```

**Child purchase approval**
```
child: POST /events/{id}/rsvp
  └─ RsvpService detects dependent + approval required
      └─ INSERT purchase_approvals(pending, expires_at=+48h) → 202 {approval_id}
         enqueue notify_parent_approval_request

parent: POST /me/approvals/{id}/approve   (Idempotency-Key required)
  ├─ SELECT ... FOR UPDATE approval; assert pending & not expired
  ├─ call RsvpService.confirm(...) / PaymentService.charge(...)
  ├─ UPDATE approval SET status=approved, responded_at=now()
  └─ COMMIT → enqueue notify_child_decision
```

**Refresh rotation with reuse detection**
```
POST /auth/refresh {token}
  ├─ hash → SELECT ... FOR UPDATE
  ├─ if consumed_at IS NOT NULL:  UPDATE refresh_tokens SET revoked_at=now() WHERE family_id=:f
  │                               → 401 REFRESH_REUSE_DETECTED + audit
  ├─ if expired/revoked → 401
  └─ stamp consumed_at, INSERT successor in same family, return new pair
```

---

## 12. Background jobs

| Job | Trigger | Payload | Effect | Idempotency key |
|:---|:---|:---|:---|:---|
| `send_trainer_invite` | US-01.01 | `user_id`, `setup_token_id` | invite email | `setup_token_id` |
| `send_welcome_email` | US-01.02 | `user_id` | welcome email | `user_id + 'welcome'` |
| `send_verification_email` | registration, resend | `user_id`, `token_id` | verification email | `token_id` |
| `send_password_reset` | reset request | `user_id`, `token_id` | reset email | `token_id` |
| `notify_parent_approval_request` | US-01.05 | `approval_id` | email + in-app | `approval_id + 'requested'` |
| `notify_child_decision` | US-01.05 | `approval_id` | notify child | `approval_id + status` |
| `notify_parent_share_link_blocked` | US-01.06 | `player_profile_id`, `share_link_id` | parent email | both ids |
| `expire_purchase_approvals` | cron, every 5 min | — | `pending` → `expired` past `expires_at` | per-row status guard |
| `expire_impersonation_sessions` | cron, every 5 min | — | close sessions past `expires_at`, revoke `jti` | per-row guard |
| `purge_expired_tokens` | cron, nightly | — | delete refresh/verification rows past retention | — |

---

## 13. Performance & scale targets

| Operation | Target | Verification |
|:---|:---|:---|
| `POST /auth/login` | p95 < 300 ms (argon2 dominates) | k6, 50 RPS |
| `GET /me` | p95 < 100 ms | k6 |
| `GET /admin/users?limit=50` over 10 000 users | p95 < 500 ms | seeded dataset + `EXPLAIN ANALYZE` showing index scan |
| `POST /auth/register` (ShareLink) | p95 < 400 ms | k6, 100 concurrent |
| Concurrent registrations on one static link | 200 concurrent, 0 lost writes | e2e concurrency test |

Every target above is asserted by a script in `tests/load/`.

---

## 14. Open questions

| ID | Question | Priority | Blocks |
|:---|:---|:---:|:---|
| Q-01.01 | Skill-level enum values — fixed set or trainer-defined? | P2 | `player_profiles.skill_level` column type |
| Q-01.02 | Age grouping — birth year, age range, or grade? | P2 | Epic-02 eligibility query shape |
| Q-01.04 | Which transactional emails are required at launch? | P1 | job list §12 |
| Q-01.05 | Is email verification a hard gate on login, or only on purchase endpoints? | P1 | auth dependency wiring |
| Q-01.06 | Notify a coach when a trainer overrides their availability? | P2 | Epic-02 job list |
| Q-01.07 | Refresh token TTL — 7 / 30 / 90 days? | P2 | `refresh_tokens.expires_at` default |

Assumption taken where unresolved, documented in code: Q-01.05 → verification gates purchase and
ShareLink redemption but not login; Q-01.07 → 30 days.

---

## 15. Epic Definition of Done

Conventions §10 plus:

- [ ] All 14 user stories implemented with the endpoint contracts in §8
- [ ] Permission matrix (§6) fully covered — one test per ❌ and per "own tenant" cell
- [ ] Cross-tenant isolation tests return `404` on every tenant-scoped route
- [ ] Concurrency tests: 20-way ShareLink redemption; 10-way parallel approval approve
- [ ] Refresh-reuse detection test revokes the family
- [ ] GDPR test asserts no plaintext original email survives anywhere
- [ ] Argon2 parameters benchmarked ≥ 100 ms on target hardware
- [ ] Rate limits verified by test (`429` + `Retry-After`)
- [ ] `AvailabilityService.check_conflict` unit-tested at minute boundaries
- [ ] Load targets in §13 met against a 10 000-user seed
- [ ] `/api/v1/openapi.json` documents every endpoint with examples and error codes
