# User Management & Authentication Requirements

## Overview
The identity and access foundation of the service: authentication, refresh-token lifecycle, role-based authorization, tenant isolation, the profile graph (trainer / coach / player / parent-child), the ShareLink invitation system, availability data, admin impersonation, and GDPR-compliant erasure. This epic owns **who the caller is**, **which tenant they belong to**, and **what they may touch**. Every other epic consumes that answer through `deps.py` and never re-derives it.

## Source
User request/Epic-01 specification

## Functional Requirements
1. FR-001: User authentication system
   - Acceptance: Secure login/logout, password reset, email verification, forced password change, rate limiting
   - Priority: High

2. FR-002: Role-based authorization with 4 roles (super_admin, trainer, coach, player)
   - Acceptance: Permission matrix enforced, require_role/require_tenant_scope dependencies work correctly
   - Priority: High

3. FR-003: Tenant isolation via trainer_player_links table
   - Acceptance: All user-scoped routes return 404 for cross-tenant access, zero cross-tenant reads in test suite
   - Priority: High

4. FR-004: User management for Super Admin
   - Acceptance: CRUD operations on users, trainer provisioning, activation/deactivation, GDPR erasure
   - Priority: High

5. FR-005: Player/parent functionality
   - Acceptance: Dependent profile CRUD, trainer associations, purchase approval workflow, child login constraints
   - Priority: High

6. FR-006: Coach functionality
   - Acceptance: Single trainer association, availability windows, public profile fields
   - Priority: Medium

7. FR-007: Trainer functionality
   - Acceptance: ShareLink issuance (static/unique), player availability read, own org user management, portal branding
   - Priority: High

8. FR-008: ShareLink invitation system
   - Acceptance: Secure code generation, redemption tracking, analytics, kind-based restrictions (player_static/coach_unique)
   - Priority: High

9. FR-009: Availability windows system
   - Acceptance: Weekly schedule per player/coach, conflict detection, timezone-aware storage
   - Priority: Medium

10. FR-010: Purchase approval workflow
    - Acceptance: 48-hour expiry, parent approval/deny, child-initiated requests requiring approval
    - Priority: High

11. FR-011: Admin impersonation system
    - Acceptance: 60-minute limited tokens, audit logging, X-Impersonated-By header, admin route restrictions
    - Priority: Medium

12. FR-012: GDPR-compliant erasure
    - Acceptance: PII anonymization in-place, referential integrity maintained, user_erasures ledger
    - Priority: High

## Non-Functional Requirements
1. NFR-001: Performance targets
   - Metric: p95 POST /auth/login < 300ms; p95 authenticated GET on profile endpoints < 200ms; p95 GET /admin/users?limit=50 over 10k users < 500ms

2. NFR-002: Security standards
   - Metric: Argon2id parameters (time_cost=3, memory_cost=64 MiB, parallelism=4) ≥ 100ms verification; no user enumeration; constant-time failure paths; refresh token reuse detection

3. NFR-003: Concurrency and scale
   - Metric: 200 concurrent ShareLink redemptions with 0 lost writes; 10-way parallel approval approval; rate limiting per Conventions §4

4. NFR-004: Tenancy isolation
   - Metric: 0 cross-tenant reads in isolation test suite

## Business Rules
1. BR-001: Role permissions matrix (§6) enforced at service layer - each ❌ and "own tenant" cell tested
2. BR-002: Trainer accounts created only by super_admin
3. BR-003: Player may link to many trainers but data access is tenant-scoped per trainer_id
4. BR-004: Coach active under exactly one trainer (partial unique index)
5. BR-005: Every profile with is_dependent and age < 18 owned by parent account
6. BR-006: Approval requests expire at 48h and cannot be resurrected
7. BR-007: Impersonation never targets super_admin, capped at 60min, every request audited
8. BR-008: Erasure anonymizes in-place, never DELETE FROM users, FKs ON DELETE RESTRICT throughout schema

## Task Breakdown

### Models
| Model | Properties | Relations |
|-------|------------|-----------|
| users | id (UUID PK), email (citext UNIQUE), password_hash, role (enum), status (enum), is_email_verified, must_change_password, last_login_at, deleted_at | 1:1 with profiles |
| profiles | user_id (PK/FK), first_name, last_name, phone, avatar_key, school, timezone | 1:1 with users |
| trainer_profiles | user_id (PK/FK), business_name, slug (UNIQUE), address, website, description, logo_key, brand colors | 1:1 with users |
| coach_profiles | user_id (PK/FK), trainer_id (FK→trainer_profiles), bio, credentials, certifications (JSONB), is_public, joined_at | 1:1 with users, N:1 with trainer_profiles |
| player_profiles | id (PK), owner_user_id (FK→users), login_user_id (FK→users nullable), display_name, birth_date, gender, skill_level, school, jersey_number, is_dependent, emergency contact, token_spend_requires_approval | owner_user_id→users, login_user_id→users nullable |
| trainer_player_links | id (PK), trainer_id (FK), player_profile_id (FK), source (enum), share_link_id (FK nullable), linked_at, status (enum) | trainer_id→trainer_profiles, player_profile_id→player_profiles, share_link_id→share_links |
| share_links | id (PK), code (text UNIQUE), trainer_id (FK), created_by_user_id (FK), kind (enum), target_email, expires_at, max_uses, use_count, is_active | trainer_id→trainer_profiles, created_by_user_id→users |
| availability_windows | id (PK), subject_type (enum), subject_id (UUID), weekday (0-6), start_time, end_time, is_available |  |
| purchase_approvals | id (PK), player_profile_id (FK), parent_user_id (FK), trainer_id (FK), subject_type (enum), subject_id (UUID), amount_cents, token_amount, status (enum), requested_at, responded_at, expires_at, parent_note | player_profile_id→player_profiles, parent_user_id→users, trainer_id→trainer_profiles |
| impersonation_sessions | id (PK), admin_user_id (FK), target_user_id (FK), started_at, ended_at, expires_at, reason, jti | admin_user_id→users, target_user_id→users |
| user_erasures | id (PK), original_user_id (UUID), original_email_hash (SHA-256), erased_by_user_id (FK), reason, erased_at, legal_retention_until | erased_by_user_id→users |
| refresh_tokens | id (PK), user_id (FK), family_id (UUID), token_hash (SHA-256 UNIQUE), expires_at, consumed_at, revoked_at, user_agent_hash, ip_hash | user_id→users |

### Services
| Service | Purpose | Methods |
|---------|---------|---------|
| AuthService | Handle authentication flows | login, logout, refresh, password reset, email verification, ShareLink registration |
| UserService | User CRUD operations | create trainer, get users, update user, deactivate/reactivate, erase (GDPR) |
| ProfileService | Profile management | get/update own profile, avatar handling |
| PlayerService | Player/parent specific operations | create dependent, manage trainer associations, purchase approvals, child login |
| CoachService | Coach specific operations | availability management, public profile |
| TrainerService | Trainer specific operations | ShareLink issuance, branding, player management |
| ShareLinkService | Invitation system | code generation, redemption, analytics |
| AvailabilityService | Schedule management | window storage, conflict detection |
| ApprovalService | Purchase approval workflow | request creation, parent approval/deny, expiry job |
| ImpersonationService | Admin impersonation | session creation, exit, audit logging |
| AuditService | Cross-cutting concerns | audit logging for security events |
| EmailService | Notification delivery | trainer invites, welcome, verification, password reset, approval notifications |

### Endpoints
| Resource | Endpoints | Purpose |
|----------|-----------|---------|
| auth | POST /api/v1/auth/login | Email/password login with rate limiting |
| auth | POST /api/v1/auth/refresh | Rotating refresh token with reuse detection |
| auth | POST /api/v1/auth/logout | Revoke refresh family |
| auth | POST /api/v1/auth/password-reset/request | Password reset request (no user enumeration) |
| auth | POST /api/v1/auth/password-reset/confirm | Password reset confirmation (single-use) |
| auth | POST /api/v1/auth/verify-email | Email verification |
| auth | POST /api/v1/auth/verify-email/resend | Verification email resend (rate limited) |
| auth | POST /api/v1/auth/register | Public registration via ShareLink or camp submission |
| auth | POST /api/v1/auth/impersonation/exit | End impersonation session |
| me | GET/PATCH /api/v1/me | Own profile management |
| me | PUT /api/v1/me/avatar | Two-phase avatar upload |
| me | GET/PUT /api/v1/me/availability | Weekly availability management (player/coach) |
| me | GET /api/v1/me/contexts | Available trainer contexts for player |
| me | GET/POST /api/v1/me/players | Dependent profiles (list/create) |
| me | GET/PATCH/DELETE /api/v1/me/players/{id} | Individual dependent profile management |
| me | POST /api/v1/me/players/{id}/trainers | Add trainer to dependent |
| me | DELETE /api/v1/me/players/{id}/trainers/{trainer_id} | Remove trainer from dependent |
| me | GET /api/v1/me/approvals | Pending purchase approvals (parent view) |
| me | POST /api/v1/me/approvals/{id}/approve | Approve purchase request |
| me | POST /api/v1/me/approvals/{id}/deny | Deny purchase request |
| trainer | GET/POST /api/v1/trainer/share-links | List/issue ShareLinks (trainer scoped) |
| trainer | DELETE /api/v1/trainer/share-links/{id} | Deactivate ShareLink |
| trainer | GET /api/v1/trainer/players/{id}/availability | Read player availability (trainer scoped) |
| trainer | GET/PATCH /api/v1/trainer/branding | Portal branding management |
| public | GET /api/v1/public/share-links/{code} | ShareLink preview (unauthenticated) |
| public | GET /api/v1/public/trainers/{slug}/branding | Trainer branding (unauthenticated, cached) |
| admin | GET /api/v1/admin/users | Super admin user directory (paginated, filtered) |
| admin | POST /api/v1/admin/users/trainers | Provision new trainer account |
| admin | GET/PATCH /api/v1/admin/users/{id}| Individual user management |
| admin | POST /api/v1/admin/users/{id}/deactivate | Deactivate user |
| admin | POST /api/v1/admin/users/{id}/reactivate | Reactivate user |
| admin | DELETE /api/v1/admin/users/{id} | GDPR erasure |
| admin | POST /api/v1/admin/users/{id}/impersonate | Begin impersonation session |

## Gap Analysis
- [ ] Clarify skill-level enum values — fixed set or trainer-defined? (Q-01.01)
- [ ] Determine age grouping approach — birth year, age range, or grade? (Q-01.02)
- [ ] Confirm which transactional emails are required at launch? (Q-01.04)
- [ ] Finalize email verification gate scope — login vs purchase/ShareLink redemption? (Q-01.05)
- [ ] Determine if coaches should be notified when trainer overrides their availability? (Q-01.06)
- [ ] Finalize refresh token TTL — 7 / 30 / 90 days? (Q-01.07)