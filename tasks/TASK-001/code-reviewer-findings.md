# Code Review — TASK-001, round 2 (fixes for the round-1 findings)

Branch: `feature/epic-01-identity-auth` (worktree `.worktrees/epic-01-identity-auth`). This round reviews
the **uncommitted** working-tree diff on top of `89ce04f`: 9 files, +165/-12.

Scope:
- Admin deactivation now ends impersonation sessions.
- `PrincipalService` checks the `act` admin's state.
- Erasing a child login anonymises its `PlayerProfile` and cuts its trainer links.
- Self and peer super-admin guards on deactivate and erase.
- `PENDING` reactivation is rejected.

## Summary

The four targeted round-1 findings are fixed correctly and each has e2e tests:
- Major 1: impersonation outliving admin deactivation.
- Major 2: child-login PII left on its `PlayerProfile`.
- Major 3: self and peer super-admin deactivate/erase.
- Minor 1: `PENDING` -> `ACTIVE` reactivation.

The guards run inside the UoW after the row lock and before any mutation. A failed guard therefore
writes no audit or ledger row, and the tests assert this. The `act` re-check in `resolve()` is a
sound defence in depth, and it is the only path that covers an admin deactivated out of band.

What remains:
- The API contract (spec + error registry + CHANGELOG) was not updated for the three new error
  codes and the new child-erasure semantics.
- A small GDPR/integrity gap remains: pending approvals for an erased child's profile stay open.

Counts: **Critical 0 · Major 1 · Minor 4**

## Round-1 status

| Round-1 finding | Status |
|---|---|
| [Major] Impersonation token outlives the admin's deactivation | FIXED: `admin_user_service.py:173-175` + `principal_service.py:48-56`; test `test_impersonation_token_dies_when_the_impersonating_admin_is_deactivated` |
| [Major] Child-login erasure leaves PII on `PlayerProfile` | FIXED: `erasure_repo.player_profiles_of` (owner OR login) + `deactivate_links`; tests for the child and for a parent's dependents |
| [Major] Super-admin can deactivate/erase self or a peer | FIXED: `_guard_target` in both services (409 `CANNOT_TARGET_SELF` / 403 `CANNOT_MODIFY_ADMIN`); tests assert no state change and no ledger row |
| [Minor] `reactivate` promotes `PENDING` -> `ACTIVE` | FIXED: 409 `USER_PENDING` (`admin_user_service.py:187-189`); tested |
| Other round-1 Minors (rate limits, Content-Type on copy, SVG attribute denylist, branding cache, S3 I/O in tx, 2-3 DB sessions per request, email-change notice, LoggingEmailSender) | OPEN: out of scope for this change |

## Issues Found

### Critical (Must Fix)

None.

### Major (Should Fix)

- [ ] **[Major] The API contract does not match the new behaviour.** Locations:
  `specs/api-designer-spec.md:645-664` (deactivate / reactivate / DELETE status lists),
  `specs/api-designer-spec.md:688` (error code registry) and `CHANGELOG.md` (unchanged).
  - The code now returns 409 `CANNOT_TARGET_SELF`, 403 `CANNOT_MODIFY_ADMIN` and 409 `USER_PENDING`.
    None of them appears in the endpoint sections or in the registry.
  - The DELETE section still says only "dependents of an erased parent erased in same tx". It does
    not say that erasing a child login anonymises the parent-owned profile and deactivates its
    trainer links.
  - Clients and the OpenAPI contract both treat the spec as the source of truth, so the docs and
    the code have drifted.

  **Fix:**
  - Add the three codes to the status lines of `POST .../deactivate`, `POST .../reactivate` and
    `DELETE /admin/users/{id}`, and to the 403/409 rows of the registry.
  - Add one sentence on child-login erasure.
  - Add a CHANGELOG `[Unreleased]` entry.

### Minor (Consider)

- [ ] **[Minor] Pending approvals for an erased child's profile stay PENDING and can still be
  approved.** Locations: `app/services/erasure_service.py:91`, `app/repositories/erasure_repo.py:134`.
  - `expire_pending_approvals` filters only on `parent_user_id == user.id`.
  - When a child login is erased, the approvals for its profile are keyed to the *parent*, so they
    are never expired.
  - `ApprovalService._decide` (`approval_service.py:118-133`) does not check
    `PlayerProfile.is_active`. The parent can therefore approve a purchase for an anonymised,
    inactive profile.

  **Fix:**
  - Also expire by `PurchaseApproval.player_profile_id.in_(dependent_ids)`. One `or_` in the repo
    method covers both the parent and the child cases.
  - Add an assertion to `test_erasing_a_child_login_anonymises_its_profile_and_cuts_trainer_links`.

- [ ] **[Minor] There is no longer an API path to disable a compromised super-admin, and the
  admin-side session cleanup cannot be reached.** Locations: `app/services/admin_user_service.py:118-123`,
  `:173-175`.
  - `_guard_target` forbids deactivating any `SUPER_ADMIN`, so `end_all_for_user` in `deactivate()`
    never runs for an admin through the API.
  - The comment "Sessions where the user is ... the impersonating admin die with it" describes a
    case that cannot happen.
  - Only the `resolve()` `act` check protects against an admin deactivated out of band. That check
    is correct and tested.
  - This trade-off is a product decision; this review does not make it.

  **Fix:**
  - Either document the break-glass procedure (DB/CLI) in the spec and correct the comment...
  - ...or allow *peer* deactivation, which is reversible, while still blocking self-deactivation and
    erasure.

- [ ] **[Minor] Duplicate logic.** Locations: `app/repositories/erasure_repo.py:39-47` and
  `app/services/admin_user_service.py:117-123` vs `app/services/erasure_service.py:112-118`.
  - `ErasureRepo.deactivate_links` duplicates `TenancyRepo.deactivate_links_for_player`
    (`tenancy_repo.py:159`), in a bulk form.
  - The self/super-admin guard is copied across the two services with different messages.

  **Fix:**
  - Make the tenancy method accept a list and reuse it.
  - Move the guard into one helper (for example `app/core/admin_guards.py`) that takes the verb for
    the message.

- [ ] **[Minor] Impersonated requests now make one more DB round-trip.** Location:
  `app/services/principal_service.py:48`.
  - `get_auth_state(act)` runs after `get_active(jti)`, which adds a third read on the
    already-heavier impersonated path. See round-1 minor "2-3 DB sessions per request".
  - The extra cost is acceptable, since impersonation traffic is low.

  **Fix (optional):** have `ImpersonationRepo.get_active` join `users` on `admin_user_id` and filter
  `status=ACTIVE AND role=SUPER_ADMIN` in a single query.

### Positive Notes

- Each guard runs after the row lock and before any write. A rejected request therefore leaves no
  audit or ledger row, and the tests check this explicitly
  (`select(UserErasure)` is empty, status stays `active`).
- `player_profiles_of` uses one `OR` query, so erasing a child login also runs the commitment check
  on the child's own profile. This closes a second, quieter gap: a child with active RSVPs could
  previously be erased.
- `deactivate_links` is a single set-based UPDATE (no N+1) with an early return on an empty list.
- `deactivate()` reads `now` once and reuses it for the token revocation and the session end, so the
  timestamps match.
- The new error codes match the existing conventions (`CANNOT_IMPERSONATE_ADMIN` -> `CANNOT_MODIFY_ADMIN`).
  The OpenAPI `responses` were updated to include 403.
- The tests target behaviour, not implementation. In particular, the impersonation test drives the
  out-of-band deactivation with a real token.

## Verification Evidence (DOD Standard tier)

Run in `.worktrees/epic-01-identity-auth` on 2026-10-22, against the working tree:

| Check | Command | Result |
|-------|---------|--------|
| Lint | `uv run ruff check .` | PASS: "All checks passed!" |
| Format | `uv run ruff format --check .` | PASS: 149 files already formatted |
| Types | `uv run mypy` (strict) | PASS: no issues in 94 source files |
| Tests | `uv run pytest -q` | PASS: 546 passed in 133.00s (+7 vs round 1) |
| Naming | `tasks/TASK-001/*` | PASS: all skill-prefixed |
| Spec sync | `specs/api-designer-spec.md`, `CHANGELOG.md` | FAIL: see Major finding |
