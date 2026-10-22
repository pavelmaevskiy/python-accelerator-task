# Code Review — TASK-001 (re-review)

Scope: uncommitted diff on `main` — `CHANGELOG.md`, `Task/Epics/00_Backend_Conventions.md`,
`Task/Epics/Epic-01_User_Management_Authentication_SPEC.md` (spec/docs only, no Python code).
This pass re-checks the previous review after the fixes for the self-impersonation check order and
approval expiry on erasure.

Confirmed product decision (not re-opened here): erasing only a child's login user anonymises that
`users` row. The dependent `player_profiles` row and its owner stay untouched.

## Summary

Both previous Major findings are fixed.

- **US-01.07 check order.** `CANNOT_TARGET_SELF` is now evaluated first, and the role criterion
  covers only *another* `super_admin`, so the two outcomes no longer overlap. The order is also
  explained in the spec.
- **Approval expiry on erasure (US-01.13).** The rule now names the join: `owner_user_id` **or**
  `login_user_id`. It lists the three cases it covers and states the confirmed no-cascade behaviour.
- **Parent/child invariant.** It now matches the acceptance criterion. This was a previous Minor.

One new wording conflict comes from the confirmed decision: the generic anonymisation bullet still
says erasure renames `player_profiles.display_name`. Two previous Minors are still open, and the
CHANGELOG wording is unchanged.

Counts: 0 Critical, 0 Major, 4 Minor.

## Status of previous findings

| Previous finding | Status |
|:---|:---|
| [Major] US-01.07 order of self check vs. role check | Fixed (`:368-369`) |
| [Major] Unclear which profiles count as "that user's profiles" for approval expiry | Fixed (`:439`) |
| [Minor] Registry looks complete but holds only the 3 new codes | Open |
| [Minor] Registry "Raised when" column repeats Epic-01 text | Open |
| [Minor] Invariant narrower than the acceptance criterion | Fixed (`:479`) |
| [Minor] CHANGELOG scope and "now expires" wording | Open |

## Issues Found

### Critical (Must Fix)
- None.

### Major (Should Fix)
- None.

### Minor (Consider)

- [ ] **[Minor]** `Task/Epics/Epic-01_User_Management_Authentication_SPEC.md:434`: the first US-01.13
  criterion says erasure sets `player_profiles.display_name → "Deleted Player"` and doesn't say which
  profiles it applies to. The new criterion at `:439` says erasing a child's login user "leaves the
  dependent profile … untouched". An implementer who reads `:434` as "every profile linked to the
  erased user", including through `login_user_id`, would rename the child's profile. That breaks the
  confirmed decision, and the tests disagree.
  **Fix:** limit `:434`: "`player_profiles.display_name` → `"Deleted Player"` for profiles whose
  `owner_user_id` is the erased user (profiles that only reference them via `login_user_id` are not
  modified)".

- [ ] **[Minor]** `Task/Epics/Epic-01_User_Management_Authentication_SPEC.md:439` / US-01.06 `:356`:
  after the decision, a dependent profile keeps `login_user_id` pointing at a `deleted` user. Nothing
  says whether the parent can give the child a new login later by repointing `login_user_id`
  (US-01.06), or whether the old pointer blocks that. This is not a bug today, but it is undefined
  behaviour that Epic-01 tests will run into.
  **Fix:** add one line to US-01.06 or US-01.13, e.g. "A parent may set a new login for a dependent
  whose `login_user_id` references a `deleted` user; this replaces the pointer."

- [ ] **[Minor]** `Task/Epics/00_Backend_Conventions.md:157-164`: still open. The intro calls the
  registry an index of "codes added by the admin user-management rules", but it leaves out
  `CANNOT_IMPERSONATE_ADMIN`, `USER_ERASED`, `ALREADY_ERASED` and `IMPERSONATION_EXPIRED` from the
  same stories. The "Raised when" column also repeats the Epic-01 trigger text, which AGENTS.md
  forbids ("MUST NOT duplicate information across spec files — reference instead").
  **Fix:** either list every admin user-management code, or narrow the intro to say the table is
  partial. Cut "Raised when" to a short label or drop it.

- [ ] **[Minor]** `CHANGELOG.md:14`: still open. "Erasing a user now expires…" reads as if the
  behaviour shipped, but only the spec changed. The entry also says nothing about the US-01.07 check
  order, which this task changed. Separately, confirm that `Task/Epics/` spec edits belong in the
  framework CHANGELOG at all.
  **Fix:** reword it, e.g. "Specify that erasing a user expires pending purchase approvals on profiles
  they own or log in to (Epic-01 US-01.13), and that impersonation checks `CANNOT_TARGET_SELF` before
  the admin-role check (US-01.07)".

### Positive Notes
- The US-01.07 criterion explains *why* the order matters (the caller is always a `super_admin`).
  This stops a later edit from undoing it without anyone noticing.
- US-01.12 and US-01.13 cover the self case and the other-admin case without overlap, so every
  self/admin target gets exactly one error code.
- The erasure-expiry criterion is precise and safe to implement:
  - It names the two FK paths.
  - It runs in the same transaction, leaves `responded_at` null and enqueues no notification.
  - It reuses the existing `expired` status and `409 APPROVAL_NOT_PENDING`.
  - It stays compatible with the sweeper's per-row status guard (pending → expired from either side
    is idempotent).
- It writes down the no-cascade decision ("`login_user_id` keeps pointing at the anonymised row"),
  which matches the "FKs are never cascaded" / `ON DELETE RESTRICT` erasure invariant.
- Authorization: the new guards only make the admin endpoints stricter. No OWASP regressions are
  introduced at spec level.

## Verification Evidence (DOD.md, Standard tier)

| Check | Result |
|:---|:---|
| `git diff --stat` | 3 files, +20/-3, all in scope of the task |
| `git diff --check` | clean |
| `pytest` / `ruff` / `mypy` | N/A: no `pyproject.toml`, the change is Markdown only |
| New test coverage | N/A: no code changed |
| Cross-refs | `APPROVAL_NOT_PENDING` (US-01.05), `player_profiles.owner_user_id`/`login_user_id` (`:175-177`), `purchase_approvals.status`/`responded_at` (`:215-219`), sweeper job (`:541`) all exist and match |
| File naming | report at `tasks/TASK-001/code-reviewer-findings.md` (skill-prefixed, zero-padded task dir); `.task-counter` = 1, unchanged |
