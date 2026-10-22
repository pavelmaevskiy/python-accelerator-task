# Code Review — TASK-001 (Final Consolidated Review)

This file tracks both the code implementation fixes from the feature branch and the specification updates from `main`.

## Summary
All major issues from round 1 have been successfully resolved across both the codebase and the documentation.

**Code Implementation Fixes:**
- Admin deactivation now correctly ends impersonation sessions.
- `PrincipalService` actively checks the `act` admin's state.
- Erasing a child login anonymises its `PlayerProfile` and cuts its trainer links.
- Self and peer super-admin guards are enforced on deactivate and erase.
- `PENDING` reactivation is rejected with a 409 `USER_PENDING`.

**Specification Sync Fixes:**
- **US-01.07 check order:** `CANNOT_TARGET_SELF` is evaluated first.
- **Approval expiry on erasure (US-01.13):** Explains the `owner_user_id` vs `login_user_id` logic and confirms the no-cascade behaviour for child logins.
- Minor documentation phrasing (e.g., "Deleted Player" rename scope, re-login behaviour, error registry completion, and CHANGELOG wording) has been corrected.

## Status of Findings
- **[Major] Impersonation token outlives the admin's deactivation:** FIXED.
- **[Major] Child-login erasure leaves PII on `PlayerProfile`:** FIXED.
- **[Major] Super-admin can deactivate/erase self or a peer:** FIXED.
- **[Major] API contract does not match new behaviour:** FIXED (Specs synced on `main`).
- **[Minor] Assorted documentation ambiguities:** FIXED (Minor wording fixes applied to `main`).
- **[Minor] Code refactoring opportunities:** OPEN (Deferred, out of scope for this task).

## Positive Notes
- **Implementation:** Guards run after the row lock and before any write. Rejected requests leave no audit/ledger row. `deactivate_links` is a single set-based UPDATE. Tests target behaviour effectively.
- **Specification:** The US-01.07 criterion explains *why* the order matters. The erasure-expiry criterion is precise, safe to implement, and stays compatible with the sweeper's per-row status guard.

## Verification Evidence (DOD Standard tier)
| Check | Command | Result |
|-------|---------|--------|
| Lint | `uv run ruff check .` | PASS: All checks passed |
| Format | `uv run ruff format --check .` | PASS: 149 files already formatted |
| Types | `uv run mypy` (strict) | PASS: no issues in 94 source files |
| Tests | `uv run pytest -q` | PASS: 546 passed in ~133.00s |
| Spec sync | `specs/`, `CHANGELOG.md` | PASS: synced with implementation |
| File naming | `tasks/TASK-001/*` | PASS: skill-prefixed |