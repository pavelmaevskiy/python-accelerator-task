# API Specification

**Base path:** `/api/v1` · **Format:** JSON · **Auth:** `Authorization: Bearer <access JWT>` unless marked public.
**Source of truth for behaviour:** `architect-architecture.md` (binding corrections C1-C8) over
`tasks/TASK-001/brainstorm-design.md`; baseline conventions in `Task/Epics/00_Backend_Conventions.md`.
This file records only the HTTP contract: paths, schemas, status codes, OpenAPI metadata.

---

## Global conventions (apply to every endpoint)

### Deviations from the api-designer skill defaults (conventions doc wins)

| Skill default | This project | Why |
|---|---|---|
| Error body `{status_code, message, error, error_code, details[]}` | `{"error": {"code", "message", "details"}}` | Conventions §5, single exception handler |
| Offset pagination `page`/`limit` | Cursor: `{items, next_cursor, has_more}`, `limit` default 50, max 200 | Conventions §5 |
| `PUT` full update on resources | `PATCH` only; `PUT` kept **only** for replace-whole-set sub-resources (`/me/availability`, `/me/players/{id}/availability`) and the presign call `/me/avatar` | Epic-01 §8 mandates these verbs (see Open items O1) |
| Int ids | UUIDv7 strings | Conventions §5 |
| DELETE returns 204 | `204` for deactivations/removals; erasure `DELETE /admin/users/{id}` returns `204` | |

### Error envelope

```python
class ErrorBody(BaseModel):
    code: str                                  # e.g. "EMAIL_TAKEN"
    message: str
    details: dict[str, Any] | None = None      # 422: {"errors": [{"loc": [...], "msg": "...", "type": "..."}]}

class ErrorResponse(BaseModel):
    error: ErrorBody
```

Status mapping (centralised handler; handlers never build error bodies): 401 `AuthenticationError`,
403 `PermissionDeniedError`, 404 `NotFoundError` (also cross-tenant), 409 `ConflictError`,
422 `DomainValidationError` / Pydantic, 429 `RateLimitedError` (+ `Retry-After`).

### Shared base schemas

```python
class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")          # every REQUEST model

class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)    # every RESPONSE model

class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None
    has_more: bool

class CursorParams(BaseModel):                          # query dependency
    limit: int = Field(50, ge=1, le=200)
    cursor: str | None = None

Password = Annotated[str, Field(min_length=12, max_length=128)]   # + zxcvbn >= 3 in service (422 WEAK_PASSWORD)
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
Timezone = Annotated[str, AfterValidator(validate_iana_tz)]       # zoneinfo.available_timezones()
Slug = Annotated[str, Field(min_length=3, max_length=63, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
```

### Cross-cutting headers

| Header | Direction | Use |
|---|---|---|
| `Authorization` | req | Bearer access token |
| `X-Trainer-Context` | req | Player's selected trainer (UUID). Validated against active `trainer_player_links`; mismatch -> `404`. Never read from body |
| `Idempotency-Key` | req | Required on approve/deny; replay returns stored response |
| `X-Impersonated-By` | resp | Admin id, on every response to an `act` token |
| `Retry-After` | resp | On 429 |
| `Cache-Control` | resp | `public, max-age=300` on public branding |

### Role legend

`public` · `any` (authenticated) · `player` (includes parents; `is_child` restricted where noted) · `trainer` · `coach` · `super_admin` · `impersonated`.
Role guards are router-level dependencies (C7); object/tenant checks are in services and raise `404`.

### Rate limits (Redis fixed window, `429` + `Retry-After`)

login 10/min/IP + 5/min/email · password-reset 5/h/email · verify-resend 3/h/email (+per IP) ·
public share-link preview 60/min/IP · default 300/min/principal.

---

## Module: Auth (`api/v1/auth`, tag `auth`)

```python
# schemas
class TokenPair(ORMModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = 900

class UserSummary(ORMModel):
    id: UUID; email: EmailStr; role: UserRole; status: UserStatus
    first_name: str; last_name: str; is_email_verified: bool; must_change_password: bool
```

### [TASK-001] POST /auth/login (2026-10-21)

**Domain:** Auth · **Role:** public · **Tags:** auth · **Rate limit:** 10/min/IP, 5/min/email

```python
class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

class LoginResponse(TokenPair):
    user: UserSummary
```

**Behaviour:** password verified first (dummy argon2 hash for unknown user, constant time); then
`status='active' AND is_email_verified` gate (C5).

| Status | Code | When |
|---|---|---|
| 200 | | tokens issued |
| 401 | `INVALID_CREDENTIALS` | unknown user, bad password, inactive/deleted/pending (uniform) |
| 403 | `EMAIL_NOT_VERIFIED` | correct password, unverified; no tokens issued |
| 422 | | validation |
| 429 | `RATE_LIMITED` | |

**Example 200:** `{"access_token":"eyJ...","refresh_token":"x7Qe...","token_type":"bearer","expires_in":900,"user":{"id":"0190...","email":"a@b.co","role":"player","status":"active","first_name":"Ann","last_name":"Lee","is_email_verified":true,"must_change_password":false}}`

**Router:** `@router.post("/login", response_model=LoginResponse, dependencies=[Depends(rate_limit("login"))], summary="Log in with email and password", responses={401: ErrorResponse, 403: ErrorResponse, 429: ErrorResponse})` -> `auth_service.login`.

### [TASK-001] POST /auth/refresh (2026-10-21)

**Role:** public

```python
class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=256)
# response_model: TokenPair
```

| Status | Code | When |
|---|---|---|
| 200 | | rotated: old token `consumed_at` stamped, successor issued in same family |
| 401 | `REFRESH_INVALID` | unknown / expired / revoked |
| 401 | `REFRESH_REUSE_DETECTED` | consumed token presented: whole family revoked, audit row written in separate tx |

### [TASK-001] POST /auth/logout (2026-10-21)

**Role:** any · Revokes the refresh family of the supplied token.

```python
class LogoutRequest(StrictModel):
    refresh_token: str
```

**Status:** 204 (idempotent; unknown token also 204) · 401 missing/invalid access token.

### [TASK-001] POST /auth/register (2026-10-21)

**Role:** public (optionally authenticated for the "existing account redeems another link" path) · **Tags:** auth

**Query:** exactly one of `share_code: str` (22-char) or `camp_submission_id: UUID` (Epic-08 seam; returns `501 NOT_IMPLEMENTED`-style `409 CAMP_SUBMISSION_UNAVAILABLE` until Epic-08 registers its handler).

```python
class RegisterRequest(StrictModel):
    email: EmailStr = Field(max_length=255)
    password: Password
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(None, pattern=r"^\+[1-9]\d{6,14}$")
    timezone: Timezone = "UTC"
    birth_date: date | None = None                     # own player profile (adult/parent)
    player_profile_ids: list[UUID] | None = Field(None, min_length=1, max_length=20)  # existing dependents to link (authenticated caller only)
    # coach_unique links ignore birth_date / player_profile_ids

class RegisterResponse(ORMModel):
    user: UserSummary
    tokens: TokenPair | None        # null unless email is implicitly verified (coach link with matching target_email)
    verification_required: bool     # true => verification email enqueued, login blocked until verified
    linked_trainer_id: UUID
```

**Decision D1:** the epic says "201 + token pair", but email verification is a hard login gate
(architecture, Q-01.05). Tokens are therefore issued only when the flow proves mailbox control
(`coach_unique` with matching `target_email`); otherwise `tokens=null`, `verification_required=true`.

| Status | Code | When |
|---|---|---|
| 201 | | new account created |
| 200 | | authenticated caller, existing account: only missing link created |
| 403 | `CHILD_CANNOT_REDEEM` | caller is a child login |
| 403 | `LINK_EMAIL_MISMATCH` | coach link `target_email` differs |
| 404 | `SHARE_LINK_INVALID` | unknown / inactive / expired / exhausted (one code) ; also unowned `player_profile_ids` |
| 409 | `EMAIL_TAKEN` | unique-constraint race |
| 409 | `ACCOUNT_EXISTS` | unauthenticated, email exists; `details.action="login_then_redeem"` |
| 409 | `WRONG_LINK_KIND` | coach link redeemed by non-coach flow |
| 422 | `WEAK_PASSWORD` / validation | |
| 429 | | default limit |

**Handler:** `registration_service.register(...)` (single transaction; C8 atomic `use_count` update; enqueue welcome + verification email post-commit).

### [TASK-001] POST /auth/verify-email (2026-10-21)

**Role:** public

```python
class VerifyEmailRequest(StrictModel):
    token: str = Field(min_length=20, max_length=256)
```

**Status:** 204 verified (atomic consume of `one_time_tokens` kind `email_verify`; also applies a pending `email_change` payload for kind `email_change` via the same endpoint) · 400-class collapsed to `422 TOKEN_INVALID` (unknown/expired/consumed).

### [TASK-001] POST /auth/verify-email/resend (2026-10-21)

**Role:** public · **Rate limit:** 3/h/email + per IP

```python
class ResendVerificationRequest(StrictModel):
    email: EmailStr
```

**Status:** 202 always (no enumeration) · 429.

### [TASK-001] POST /auth/password-reset/request (2026-10-21)

**Role:** public · **Rate limit:** 5/h/email

```python
class PasswordResetRequest(StrictModel):
    email: EmailStr
```

**Status:** 202 always · 429.

### [TASK-001] POST /auth/password-reset/confirm (2026-10-21)

**Role:** public

```python
class PasswordResetConfirm(StrictModel):
    token: str
    new_password: Password
```

**Status:** 204 (password set, all refresh families revoked, user implicitly email-verified) · 422 `TOKEN_INVALID` / `WEAK_PASSWORD`.
Also used to complete a trainer invite: `trainer_setup` tokens are accepted here and activate `pending -> active`, clearing `must_change_password`.

### [TASK-001] POST /auth/impersonation/exit (2026-10-21)

**Role:** impersonated (token must carry `act`) · **Status:** 204 (session `ended_at` set, `jti` revoked) · 403 `NOT_IMPERSONATING` · 401 `IMPERSONATION_EXPIRED`.

---

## Module: Current user (`api/v1/me`, tag `me`)

```python
class ProfileResponse(ORMModel):
    id: UUID; email: EmailStr; role: UserRole; status: UserStatus
    first_name: str; last_name: str; phone: str | None; school: str | None
    timezone: str; avatar_url: str | None
    is_email_verified: bool; must_change_password: bool; is_child: bool
    trainer_id: UUID | None          # resolved context (trainer/coach own; player = selected)
    trainer_profile: TrainerProfileResponse | None
    coach_profile: CoachProfileResponse | None
```

### [TASK-001] GET /me (2026-10-21)

**Role:** any · **response_model:** `ProfileResponse` · p95 < 100 ms · Status 200 / 401.

### [TASK-001] PATCH /me (2026-10-21)

**Role:** any

```python
class UpdateProfile(StrictModel):                 # role/email/status deliberately absent -> 422 on attempt
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, pattern=r"^\+[1-9]\d{6,14}$")
    school: str | None = Field(None, max_length=255)
    timezone: Timezone | None = None
    avatar_key: str | None = None                 # confirm phase of avatar upload (see PUT /me/avatar)
```

**Status:** 200 `ProfileResponse` · 404 `UPLOAD_NOT_FOUND` (avatar_key not a quarantine object of this user) · 422 `UPLOAD_INVALID` (size > 5 MB / magic bytes not jpeg|png|webp) · 422.

> **Decision D2:** confirm is the `avatar_key` field on `PATCH /me` (architecture "Uploads"), not a separate
> `POST /me/avatar/confirm` as the epic's US-01.11 text suggests. Same for `logo_key` on `PATCH /trainer/branding`.

### [TASK-001] POST /me/email-change (2026-10-21)

**Role:** any (not child) · Old email stays active until confirmation.

```python
class EmailChangeRequest(StrictModel):
    new_email: EmailStr
    current_password: str
```

**Status:** 202 (token kind `email_change`, 24 h, mailed to the new address; confirmed via `POST /auth/verify-email`) · 401 `INVALID_CREDENTIALS` · 409 `EMAIL_TAKEN`.

### [TASK-001] PUT /me/avatar (2026-10-21)

**Role:** any · Phase 1: issue presigned upload.

```python
class PresignRequest(StrictModel):
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    content_length: int = Field(gt=0, le=5 * 1024 * 1024)

class PresignResponse(ORMModel):
    upload_url: AnyHttpUrl
    upload_headers: dict[str, str]
    key: str                      # quarantine/<uuid>; send as avatar_key in PATCH /me
    expires_in: int = 300
```

**Status:** 200 · 422. (`PUT` retained per epic; semantically "obtain upload slot for the avatar resource".)

### [TASK-001] GET /me/availability and PUT /me/availability (2026-10-21)

**Role:** player, coach · Weekly set, full replace.

```python
class AvailabilityWindowIn(StrictModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    is_available: bool = True
    # model_validator: start_time < end_time

class ReplaceAvailability(StrictModel):
    windows: list[AvailabilityWindowIn] = Field(max_length=100)

class AvailabilityWindowOut(AvailabilityWindowIn, ORMModel):
    id: UUID

class AvailabilityResponse(ORMModel):
    subject_type: Literal["player", "coach"]
    subject_id: UUID
    windows: list[AvailabilityWindowOut]
```

**Status:** GET 200 · PUT 200 `AvailabilityResponse` · 403 (trainer/super_admin) · 422 `OVERLAPPING_WINDOWS` (checked in service and by gist exclusion constraint, C4; half-open `[)`) · 422.
Player subject = the caller's own (adult) profile; a child login sets its own via the same route (`is_child` allowed).

### [TASK-001] GET /me/contexts (2026-10-21)

**Role:** player

```python
class TrainerContext(ORMModel):
    trainer_id: UUID
    business_name: str
    slug: str
    logo_url: str | None
    brand_primary_color: str | None
    player_profile_id: UUID
    linked_at: datetime

class ContextsResponse(ORMModel):
    items: list[TrainerContext]       # active links only; not paginated (bounded by links per account)
```

**Status:** 200 · 403 other roles. Clients echo the chosen `trainer_id` in `X-Trainer-Context`.

### [TASK-001] GET /me/players (2026-10-21)

**Role:** player (not child) · **Query:** `CursorParams` · **response_model:** `Page[PlayerResponse]`.

```python
class PlayerLinkOut(ORMModel):
    trainer_id: UUID; business_name: str; status: Literal["active", "inactive"]; linked_at: datetime

class PlayerResponse(ORMModel):
    id: UUID; display_name: str; birth_date: date; gender: Gender
    skill_level: SkillLevel | None; school: str | None; jersey_number: int | None
    is_dependent: bool; has_login: bool
    emergency_contact_name: str | None; emergency_contact_phone: str | None
    token_spend_requires_approval: bool
    trainers: list[PlayerLinkOut]
```

### [TASK-001] POST /me/players (2026-10-21)

**Role:** player (not child; child -> `403 CHILD_CANNOT_CREATE_DEPENDENT`) · **Query:** `allow_duplicate: bool = False`

```python
class CreatePlayer(StrictModel):
    display_name: str = Field(min_length=1, max_length=100)
    birth_date: date                                    # > 1900-01-01
    gender: Gender
    school: str | None = Field(None, max_length=255)
    jersey_number: int | None = Field(None, ge=0, le=999)
    emergency_contact_name: str | None = Field(None, max_length=100)
    emergency_contact_phone: str | None = Field(None, pattern=r"^\+[1-9]\d{6,14}$")
    trainer_ids: list[UUID] = Field(default_factory=list, max_length=20)
```

**Status:** 201 `PlayerResponse` · 404 (a `trainer_ids` entry not linked to caller) · 409 `DUPLICATE_PLAYER` · 422 `INVALID_AGE` (exact y/m/d via `Clock`; valid while `1 <= age <= 18`, i.e. 18 is allowed until the 19th birthday, C2) · 422.

### [TASK-001] GET /me/players/{player_id} (2026-10-21)

**Status:** 200 `PlayerResponse` · 404 (not an owned dependent).

### [TASK-001] PATCH /me/players/{player_id} (2026-10-21)

```python
class UpdatePlayer(StrictModel):                         # all optional; birth_date change re-validates age
    display_name: str | None = Field(None, min_length=1, max_length=100)
    birth_date: date | None = None
    gender: Gender | None = None
    school: str | None = None
    jersey_number: int | None = Field(None, ge=0, le=999)
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = Field(None, pattern=r"^\+[1-9]\d{6,14}$")
    token_spend_requires_approval: bool | None = None    # parent only
```

**Status:** 200 · 404 · 409 `DUPLICATE_PLAYER` · 422 `INVALID_AGE`. `skill_level` is trainer-owned (Epic-03), absent here.

### [TASK-001] DELETE /me/players/{player_id} (2026-10-21)

Soft-deactivates the dependent (no hard delete). **Status:** 204 · 404 · 409 `ACTIVE_COMMITMENTS`.

### [TASK-001] POST /me/players/{player_id}/trainers (2026-10-21)

```python
class AddPlayerTrainer(StrictModel):
    trainer_id: UUID
```

**Status:** 201 `PlayerLinkOut` (new) · 200 (already active; idempotent) · 404 (player not owned, or trainer not linked to the parent).

### [TASK-001] DELETE /me/players/{player_id}/trainers/{trainer_id} (2026-10-21)

Sets link `status=inactive`. **Status:** 204 · 404 · 409 `ACTIVE_COMMITMENTS` (`details.rsvp_ids: list[UUID]`).

### [TASK-001] PUT /me/players/{player_id}/availability (2026-10-21)

Parent sets dependent availability. **Request/response:** `ReplaceAvailability` / `AvailabilityResponse`. **Status:** 200 · 404 (not owned) · 422 `OVERLAPPING_WINDOWS`.

### [TASK-001] GET /me/approvals (2026-10-21)

**Role:** player (parent) · **Query:** `CursorParams` + `status: ApprovalStatus = "pending"`

```python
class ApprovalResponse(ORMModel):
    id: UUID
    player_profile_id: UUID; player_display_name: str
    trainer_id: UUID; business_name: str
    subject_type: Literal["event_rsvp", "content", "token_purchase"]
    subject_id: UUID
    amount_cents: int | None; currency: Literal["usd"] = "usd"; token_amount: int | None
    status: Literal["pending", "approved", "denied", "expired"]
    requested_at: datetime; expires_at: datetime; responded_at: datetime | None
    parent_note: str | None
# response_model: Page[ApprovalResponse]
```

### [TASK-001] POST /me/approvals/{approval_id}/approve (2026-10-21)

**Role:** player (parent) · **Headers:** `Idempotency-Key` (required, 8-128 chars)

```python
class ApprovalDecision(StrictModel):
    parent_note: str | None = Field(None, max_length=500)
```

**Status:** 200 `ApprovalResponse` (replay returns stored response) · 404 (not the parent's) · 409 `APPROVAL_NOT_PENDING` · 409 `APPROVAL_SUBJECT_UNAVAILABLE` (no registered handler) · 422 missing `Idempotency-Key`.
`FOR UPDATE` on the approval -> `ApprovalSubjectHandler.on_approved` -> status update, one transaction.

### [TASK-001] POST /me/approvals/{approval_id}/deny (2026-10-21)

Same request/headers/response as approve; calls `on_denied_or_expired`. **Status:** 200 · 404 · 409 `APPROVAL_NOT_PENDING`.

---

## Module: Trainer (`api/v1/trainer`, tag `trainer`; router dependency `require_role("trainer")`)

`trainer_id` is always derived from the token (`require_tenant_scope`); never accepted from path/body.

```python
class ShareLinkResponse(ORMModel):
    id: UUID
    code: str
    url: AnyHttpUrl
    kind: Literal["player_static", "coach_unique"]
    target_email: EmailStr | None
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    is_active: bool
    created_at: datetime
```

### [TASK-001] GET /trainer/share-links (2026-10-21)

**Query:** `CursorParams`, `kind: ShareLinkKind | None`, `is_active: bool | None` · **response_model:** `Page[ShareLinkResponse]`. 200 / 403.

### [TASK-001] POST /trainer/share-links (2026-10-21)

```python
class CreateShareLink(StrictModel):
    kind: Literal["player_static", "coach_unique"]
    target_email: EmailStr | None = None     # required for coach_unique, forbidden for player_static
    # model_validator enforces kind rules; expires_at (now+7d) / max_uses (1) set server-side
```

**Status:** 201 `ShareLinkResponse` · 403 · 422. Coach link enqueues invite email to `target_email`.

### [TASK-001] DELETE /trainer/share-links/{link_id} (2026-10-21)

Deactivates (`is_active=false`). **Status:** 204 (idempotent) · 404 (other tenant's link).

### [TASK-001] GET /trainer/players/{player_id}/availability (2026-10-21)

**response_model:** `AvailabilityResponse` · **Status:** 200 · 404 (player not actively linked to this trainer, cross-tenant).

### [TASK-001] GET /trainer/branding (2026-10-21)

```python
class BrandingResponse(ORMModel):
    business_name: str; slug: str
    logo_url: str | None
    brand_primary_color: HexColor | None
    brand_secondary_color: HexColor | None
    address: str | None; website: AnyHttpUrl | None; description: str | None
```

### [TASK-001] PATCH /trainer/branding (2026-10-21)

```python
class UpdateBranding(StrictModel):
    business_name: str | None = Field(None, min_length=1, max_length=255)
    address: str | None = Field(None, max_length=500)
    website: AnyHttpUrl | None = None
    description: str | None = Field(None, max_length=2000)
    brand_primary_color: HexColor | None = None
    brand_secondary_color: HexColor | None = None
    logo_key: str | None = None            # confirm phase of logo upload
```

**Status:** 200 `BrandingResponse` · 404 `UPLOAD_NOT_FOUND` · 422 `UPLOAD_INVALID` (> 2 MB, bad magic bytes, SVG unparsable) · 422 colour. Deletes the public-branding Redis key.

### [TASK-001] PUT /trainer/branding/logo (2026-10-21)

Presign for logo. Request `LogoPresignRequest(content_type: Literal["image/png","image/svg+xml","image/webp"], content_length: int <= 2 MiB)`; response `PresignResponse`. **Status:** 200 · 422.
(Added for symmetry with `PUT /me/avatar`; the epic names only the two-phase flow without a path. See O2.)

---

## Module: Public (`api/v1/public`, tag `public`; no auth)

### [TASK-001] GET /public/share-links/{code} (2026-10-21)

**Rate limit:** 60/min/IP · records `share_link_events(kind=opened)`.

```python
class ShareLinkPreview(ORMModel):
    trainer_business_name: str
    trainer_slug: str
    logo_url: str | None
    kind: Literal["player_static", "coach_unique"]
    is_valid: Literal[True] = True
```

**Status:** 200 · 404 `SHARE_LINK_INVALID` (unknown/inactive/expired/exhausted, no oracle) · 429. Target email is never exposed.

### [TASK-001] GET /public/trainers/{slug}/branding (2026-10-21)

```python
class PublicBranding(ORMModel):
    business_name: str
    logo_url: str | None
    brand_primary_color: HexColor | None
    brand_secondary_color: HexColor | None
```

**Status:** 200 (`Cache-Control: public, max-age=300`; Redis read-through TTL 300 s) · 404. Exposes only these four fields.

---

## Module: Admin (`api/v1/admin`, tag `admin`; router dependency `require_role("super_admin")` + reject `act`)

Every mutation writes an audit row via `audit_service.record`. Impersonated principals receive `403 ADMIN_FORBIDDEN_WHILE_IMPERSONATING` on every route here.

```python
class AdminUserResponse(ORMModel):
    id: UUID; email: EmailStr; role: UserRole; status: UserStatus
    first_name: str; last_name: str; phone: str | None
    is_email_verified: bool; must_change_password: bool
    trainer_id: UUID | None; business_name: str | None
    last_login_at: datetime | None; created_at: datetime; deleted_at: datetime | None
```

### [TASK-001] GET /admin/users (2026-10-21)

```python
class AdminUserFilter(BaseModel):                  # query dependency
    role: UserRole | None = None
    status: UserStatus | None = None
    trainer: UUID | None = None                    # members of this tenant
    query: str | None = Field(None, min_length=2, max_length=100)   # trgm on email + names
    limit: int = Field(50, ge=1, le=200)
    cursor: str | None = None                      # opaque base64 of (created_at, id)
# response_model: Page[AdminUserResponse]  ; sorted created_at desc, id desc
```

**Status:** 200 · 403 · 422 bad cursor. p95 < 500 ms at 10k users.

### [TASK-001] POST /admin/users/trainers (2026-10-21)

```python
class ProvisionTrainer(StrictModel):
    email: EmailStr = Field(max_length=255)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(None, pattern=r"^\+[1-9]\d{6,14}$")
    business_name: str = Field(min_length=1, max_length=255)
    slug: Slug

class ProvisionTrainerResponse(ORMModel):
    id: UUID; email: EmailStr; business_name: str; slug: str
    status: Literal["pending"]
```

**Status:** 201 (one transaction: users + profiles + trainer_profiles; `must_change_password=true`; random unusable password never returned; `trainer_setup` token (setup TTL per C3: 7 d) mailed) · 403 · 409 `EMAIL_TAKEN` · 409 `SLUG_TAKEN` · 422.
Note: the epic text says 24 h setup TTL; architecture C3 says 7 d. Architecture is binding (see O3).

### [TASK-001] GET /admin/users/{user_id} (2026-10-21)

**Status:** 200 `AdminUserResponse` · 404.

### [TASK-001] PATCH /admin/users/{user_id} (2026-10-21)

```python
class AdminUpdateUser(StrictModel):
    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, pattern=r"^\+[1-9]\d{6,14}$")
    is_email_verified: bool | None = None
    must_change_password: bool | None = None
```

Role and status change only through dedicated actions. **Status:** 200 · 404 · 409 `USER_ERASED`.

### [TASK-001] POST /admin/users/{user_id}/deactivate (2026-10-21)

Sets `status=inactive`, revokes refresh families (access dies on next request, per-request state read). Body none. **Status:** 200 `AdminUserResponse` (idempotent) · 404 · 409 `USER_ERASED`.

### [TASK-001] POST /admin/users/{user_id}/reactivate (2026-10-21)

**Status:** 200 `AdminUserResponse` · 404 · 409 `USER_ERASED` (cannot restore deleted).

### [TASK-001] DELETE /admin/users/{user_id} (2026-10-21)

GDPR erasure (anonymise in place; dependents of an erased parent erased in same tx).

```python
class EraseUserRequest(StrictModel):      # JSON body on DELETE (supported by FastAPI/httpx); alternative: query params
    reason: str = Field(min_length=3, max_length=500)
    legal_retention_until: date | None = None
```

**Status:** 204 · 404 · 409 `ACTIVE_COMMITMENTS` · 409 `ALREADY_ERASED` (second call).

### [TASK-001] POST /admin/users/{user_id}/impersonate (2026-10-21)

```python
class ImpersonateRequest(StrictModel):
    reason: str | None = Field(None, max_length=500)

class ImpersonateResponse(ORMModel):          # NOT a TokenPair: no refresh token exists (D3, O4 resolved)
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int                            # seconds; the whole session, 3600
    expires_at: datetime                       # <= 60 min
    target: UserSummary
```

**Decision D3 (O4 resolved by the product owner):** impersonation is capped at 60 min and must not be extendable, so
**no refresh token is issued**. The access token (`act=<admin_id>`, `jti` = session id) lives for the whole session
and stays revocable because every request re-checks the `impersonation_sessions` row (`exit` or expiry -> `401
IMPERSONATION_EXPIRED`). `POST /auth/refresh` has nothing to rotate for impersonation.

**Status:** 200 · 403 `CANNOT_IMPERSONATE_ADMIN` · 403 while already impersonating · 404 · 409 `USER_ERASED` / inactive target.

---

## Error code registry (Epic-01)

| Status | Codes |
|---|---|
| 401 | `INVALID_CREDENTIALS`, `REFRESH_INVALID`, `REFRESH_REUSE_DETECTED`, `IMPERSONATION_EXPIRED`, `NOT_AUTHENTICATED`, `INVALID_TOKEN`, `ACCOUNT_INACTIVE` |
| 403 | `EMAIL_NOT_VERIFIED`, `CHILD_CANNOT_REDEEM`, `CHILD_CANNOT_CREATE_DEPENDENT`, `LINK_EMAIL_MISMATCH`, `CANNOT_IMPERSONATE_ADMIN`, `NOT_IMPERSONATING`, `ADMIN_FORBIDDEN_WHILE_IMPERSONATING`, `FORBIDDEN`, `CHILD_FORBIDDEN`, `CHILD_CANNOT_CHANGE_EMAIL` |
| 404 | `NOT_FOUND`, `SHARE_LINK_INVALID`, `UPLOAD_NOT_FOUND` |
| 409 | `EMAIL_TAKEN`, `SLUG_TAKEN`, `ACCOUNT_EXISTS`, `DUPLICATE_PLAYER`, `WRONG_LINK_KIND`, `APPROVAL_NOT_PENDING`, `APPROVAL_SUBJECT_UNAVAILABLE`, `ACTIVE_COMMITMENTS`, `ALREADY_ERASED`, `USER_ERASED`, `USER_INACTIVE`, `COACH_ALREADY_ASSIGNED`, `CAMP_SUBMISSION_UNAVAILABLE` |
| 422 | `VALIDATION_ERROR`, `INVALID_AGE`, `OVERLAPPING_WINDOWS`, `WEAK_PASSWORD`, `TOKEN_INVALID`, `UPLOAD_INVALID`, `INVALID_CURSOR`, `EXACTLY_ONE_SOURCE`, `IDEMPOTENCY_KEY_REUSED` |
| 429 | `RATE_LIMITED` |

## Permission matrix (route-level)

| Router | public | player | child | coach | trainer | super_admin | impersonated |
|---|---|---|---|---|---|---|---|
| `/auth/*` | yes (except logout, exit) | | | | | | exit only |
| `/me` (profile, avatar, email-change) | | yes | yes (no email-change) | yes | yes | yes | yes |
| `/me/availability` | | yes | yes | yes | 403 | 403 | per target |
| `/me/contexts`, `/me/players*`, `/me/approvals*` | | yes | players/approvals 403 | 403 | 403 | 403 | per target |
| `/trainer/*` | | 403 | 403 | 403 | yes | 403 | per target |
| `/public/*` | yes | | | | | | |
| `/admin/*` | | 403 | 403 | 403 | 403 | yes | 403 |

## Router layout

```
app/api/v1/
  __init__.py        api_router = APIRouter(prefix="/api/v1"); include_router(...) x5
  auth/    routes.py schemas.py     prefix=/auth     tags=[auth]
  me/      routes.py schemas.py     prefix=/me       tags=[me]       dependencies=[Depends(current_principal)]
  trainer/ routes.py schemas.py     prefix=/trainer  tags=[trainer]  dependencies=[Depends(require_role("trainer"))]
  public/  routes.py schemas.py     prefix=/public   tags=[public]
  admin/   routes.py schemas.py     prefix=/admin    tags=[admin]    dependencies=[Depends(require_role("super_admin")), Depends(reject_impersonation)]
```

Handlers stay thin: schema validation -> `service.method(principal, payload)` -> return. Common
`responses={401,403,404,409,422,429: {"model": ErrorResponse}}` are declared once per router. Every
response model is a distinct schema class (never an ORM model); `password_hash`, `token_hash`, `ip_hash`
and raw S3 keys are never present in any response.

## OpenAPI metadata

- `title="Trainer Platform API"`, `version="1.0.0"`; no `servers` entry (paths already carry the `/api/v1` prefix, so a server URL would double it)
- Security scheme `BearerAuth` (HTTP bearer, JWT); public routes override with `security=[]`.
- Tags with descriptions: `auth`, `me`, `trainer`, `public`, `admin`.
- `generate_unique_id_function`: `"{tag}_{route.name}"` for stable client SDK names.
- Header params documented: `X-Trainer-Context` (player-scoped routes), `Idempotency-Key` (approve/deny).
- Example payloads supplied via `Field(examples=[...])` / `model_config["json_schema_extra"]`.

## Open items

| # | Item | Needs |
|---|---|---|
| O1 | `PUT` used for availability replace-set and avatar/logo presign although conventions §5 says "no PUT on resources". Kept because Epic-01 §8 names them. Alternative: `PATCH`/`POST .../upload-url` | confirm with conventions owner |
| O2 | `PUT /trainer/branding/logo` path is new (epic leaves the logo presign path unnamed) | confirm |
| O3 | Trainer setup token TTL: epic US-01.01 says 24 h, architecture C3 says 7 d (binding here) | confirm |
| O4 | Impersonation token pair: whether a refresh token is issued at all (D3). **Resolved: none is issued**; `ImpersonateResponse` has no `refresh_token` field | done |
| O5 | Registration returns tokens only when mailbox control is proven (D1); client UX must handle `verification_required` | product |
| O6 | `camp_submission_id` registration depends on Epic-08; stubbed as `409 CAMP_SUBMISSION_UNAVAILABLE` | Epic-08 |
