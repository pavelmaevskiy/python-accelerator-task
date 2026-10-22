# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Specify `CANNOT_TARGET_SELF`, `CANNOT_MODIFY_ADMIN`, and `USER_PENDING` error codes in Epic-01 and add an admin user-management error registry to the backend conventions

### Changed
- Specify that erasing a user expires pending purchase approvals on profiles they own or log in to (Epic-01 US-01.13), and that impersonation checks `CANNOT_TARGET_SELF` before the admin-role check (US-01.07)
- Require Context Summary + Next Steps in the chat (never written into task/spec artifacts) and codify it in AGENTS.md (5b9f95e)

### Fixed
- Stop requirements-analyst leaking Next Steps into its written document; make inline skills relay the hand-off to chat (da4612c)
- Stop reflect/skill-creator presenting flow-style Next Steps; utilities now return to the prior phase per SKILL-FLOW (5b9f95e)

## [2.3.0] - 2026-06-23

### Added
- Add gh-issue and gh-pr helper skills for consistent GitHub issue/PR creation (316daa1)
- Add gh-release skill to consolidate [Unreleased] and publish releases

### Changed
- Make gh-pr keep CHANGELOG.md in sync, logging each change under [Unreleased]

## [2.2.0] - 2026-06-23

### Added
- Add `/context7` utility skill that fetches version-specific library docs via the `ctx7` CLI (948b366)
- Reference `/context7` from coder, architect, api-designer, and debugger so it surfaces when current library APIs matter (948b366)

## [2.1.0] - 2026-06-23

### Changed
- Defer the 11 autonomous agents' Next Steps to each skill's `## Next Steps` as the single source of truth (a454bc6)
- Reconcile and expand the flow graph so `flow-alternatives`, skill Next Steps, and `SKILL-FLOW.md` agree on one edge set (a454bc6)
- Refresh the README and make the branch-completion flow host-agnostic (f8e2d53)
- De-duplicate skill listings in the README (f10b2d3)

### Fixed
- Sharpen skill examples so snippets are copy-runnable; convert relative imports to absolute (a454bc6)
- Anchor the `git push -f` hook check to avoid false positives (2624840)

### Removed
- Stop tracking `.idea` IDE config (130b776)

## [2.0.0] - 2026-06-23

### Added
- Re-stack the framework to framework-agnostic Python on uv + ruff + pytest + mypy (566339b)
- Make skill frontmatter (`phase` / `flow-next` / `flow-alternatives` / `related`) the single source of truth for the skill graph, with enforced no-orphan/no-dead-end invariants (566339b)

### Changed
- Promote skills to the primary surface and rename them to friendly slugs; keep agents only for context isolation (566339b)
- Rewrite `SKILL-FLOW.md` and align the `moc-*` maps and `agents/README.md` with the graph invariants (566339b)
- Harden hooks to read tool-call JSON from stdin (566339b)

### Removed
- Remove the Node.js/NestJS/React stack, the frontend surface, and the RTK integration (566339b)
- Retire the legacy `.claude/commands/` layer (566339b)
- Remove the `/release` skill, the hand-maintained CHANGELOG concept, `accelerator-update`, and stale example/spec scaffolding (566339b)

## [1.1.0] - 2026-05-18

### Added
- Add context graph — MOC-based skill navigation across 4 workflow phases (3ae9432)
- Add graphs for command > agent > skill flow (3fbebda)
- Add harness engineering: stabilization cycle, Definition of Done, golden principles, and automated hooks (bc975f0)
- Add WCAG accessibility skill — 30 rules across 8 categories (3ae9432)
- Add browser-verify skill (f36b5f6)
- Add review-pr skill (2d9ec09)
- Add ctx7 CLI skill and README (f081526)
- Add RTK, high-performance CLI proxy that reduces LLM token consumption by 60-90% (6ab0c22)

### Changed
- Update skill-creator with evals and benchmarking (b7d05f5)
- Update agent-browser (84516af)
- Replace MCP usage with CLI+Skill approach (7721cb4)

### Fixed
- Fix table appearance (62c17dd)
- Fix brainstorm to ask about libraries (68df0e0)
- Fix agent-browser to use CLI (624b262)

### Removed
- Remove prompt enhancer (1aa103a)

## [1.0.0] - 2026-02-10

### Added
- Add task management system (4d6da8e)
- Add new documentation system and flow (a8ca446)
- Add changelog/release skill (d712cc5)
- Add React best practices skill (8135458)
- Add git worktrees support (f08ed46)
- Add document generation capabilities (86a4fdf)
- Add task tool integration in commands (94fde83)
- Add updated agent configurations (a2d4edc)
- Include project-generator in flow (e1416b3)
- Add new flow without orchestration (71f7071)
- Add README (dd8064f)
- Add release workflow and skill updates (ee0e08f)

### Fixed
- Fix folder structure (f566ece)
- Fix lint spec descriptions (1aa3cf1)
- Fix manifest path resolution (5360449)
- Fix absolute paths (493d639)
- Fix worktree variant selection (853b0e4)
- Simplify commands (67c6841)
- Remove unnecessary commands (c0a595d)
- Fix lint issues (1e44a91)
