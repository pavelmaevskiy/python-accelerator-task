# Accelerator Core

> **For agent policy rules, see [AGENTS.md](AGENTS.md)**

A skill-based workflow layer for [Claude Code](https://docs.anthropic.com/claude-code) that turns ad-hoc prompting into a structured, repeatable engineering process.

## What is it?

You **copy the framework into your existing project**, then work a ticket by invoking its skills.
Each skill owns one step — analyze requirements, design, plan, implement, review, test, verify,
finish — and drives the work from a ticket all the way to a merge. Heavy skills run in
**context-isolated agents**, and **quality hooks** enforce naming, block destructive commands, and
catch edit loops along the way. Framework-agnostic Python, toolchain **uv + ruff + pytest + mypy**
(no app code to ship — the product *is* the `.claude/` directory).

## Why use it

- **A flow you control.** A 4-phase pipeline (understand → plan → execute → finalize) where each
  skill stops and hands you a Context Summary — no surprise auto-chaining.
- **Context isolation.** Autonomous skills fork into their own agent, so only a short summary returns
  to your main thread — less noise, less hallucination.
- **Quality baked in.** Hooks + a tiered Definition of Done + `ruff`/`mypy`/`pytest` gates run before
  anything is called "done."
- **Copy-in install.** Drop `.claude/` + a few root files into any repo and go; nothing to build.

## The flow at a glance

```
/requirements-analyst → /brainstorm → /architect → /api-designer → /writing-plans
→ /git-worktrees → /coder → /code-reviewer → /test-generator → /verify → /finishing-branch
```

Skip what doesn't apply, jump to `/debugger` on failures. See [Skill Flow](#skill-flow) for the map.

## Architecture: Skill (+ Agent)

**Mental model:** *skills are what you invoke; agents are where the heavy ones run.* Every skill
falls into one of two buckets:

- **Autonomous skills** (`context: fork` + `agent:` in frontmatter) — invoking one **always** spawns
  its matching agent as an isolated subagent; the work never runs inline. The agent's body is the
  subagent's system prompt, the SKILL.md content is its task, and only a short Context Summary
  returns. (11 skills.)
- **Interactive skills** — `brainstorm`, `git-worktrees`, `finishing-branch`, `requirements-analyst`
  run **inline** in the main thread, because a fork can't hold the live `AskUserQuestion` dialogue
  they need. They have no agent. (4 skills.)

An autonomous skill invocation flows like this:

```
User invokes: /architect [prompt]
              │   frontmatter: context: fork, agent: architect
              ▼
   ┌──────────────────────────────┐
   │  Isolated subagent (always)   │
   │  system prompt = agent file   │
   │  task = SKILL.md (+ $ARGUMENTS)│
   └──────────────────────────────┘
              │
              ▼
   ┌──────────────────────────────┐
   │  Output: Context Summary      │
   │  + Next Steps                 │
   └──────────────────────────────┘
```

**Key benefits:**
- Every skill runs in an isolated context, deterministically (prevents hallucination, keeps the main thread clean)
- Context summaries allow handoff to the next skill
- The user controls the flow (no automatic chaining)

## Documentation System: `tasks/` vs `specs/`

The framework writes two kinds of docs, and the difference is the thing people miss most:

- **`tasks/` is scratch for *one* feature** — the requirements, design, and plan for the ticket
  you're working on right now. You throw it away when the work merges.
- **`specs/` is the project's durable memory** — the living specification of the *system* that
  agents read to get context on future tickets. You keep it; you don't reset it per feature.

| | `tasks/` | `specs/` |
|---|---|---|
| What it holds | Per-feature working docs (requirements, design, plan) | Living spec of the system (architecture, API, implementation) |
| Created by | `requirements-analyst`, `brainstorm`, `writing-plans` | `architect`, `api-designer`, `docs-generator` |
| Who reads it | You, during this one feature | Future tasks & forked agents — they load `MANIFEST.md` first for context |
| Location | `tasks/TASK-N/` (N auto-increments via `.task-counter`) | `specs/` with a `MANIFEST.md` index + focused files |
| Lifecycle | **Temporary** | **Permanent** |

### When you finish a feature

- **Delete `tasks/TASK-N/`** once the branch is merged — it was scaffolding for that ticket.
- **Keep `specs/`.** It's the context the *next* feature relies on; deleting it makes every future
  agent re-derive what the system already knows. Just make sure it reflects any
  architecture/API change — the `architect` / `api-designer` / `docs-generator` skills update it
  during the flow, and `/verify`'s Full tier checks "Specs updated if architecture/API changed".

### Won't `specs/` bloat over time?

It does grow — by design. The skills **append** `### [TASK-N] …` sections as the project evolves
(that's what "living" means). Two things keep it manageable:

1. **Modular files + a `MANIFEST.md` index** mean an agent reads only the one relevant spec file, not
   the whole folder — so growth doesn't inflate per-task context cost.
2. **`[TASK-N]` is a provenance marker, not an append-only changelog you must keep forever.** When a
   file gets unwieldy, consolidate superseded sections by hand (or via `/docs-generator`).

**Deep dive:** [`tasks/README.md`](tasks/README.md) and [`specs/README.md`](specs/README.md).
See [`spec-desc.md`](spec-desc.md) for the specification structure template.

## Prerequisites

### Python 3.11+ and uv (required)

The framework's skills assume the toolchain **uv + ruff + pytest + mypy**.

```bash
# Install uv (https://docs.astral.sh/uv/)
curl -LsSf https://astral.sh/uv/install.sh | sh

# uv manages Python itself, dependencies, and virtualenvs
uv python install 3.12
uv sync            # install project dependencies into .venv
```

`ruff`, `pytest`, and `mypy` are typically added as dev dependencies in `pyproject.toml` and run via `uv run <tool>` (or directly once the venv is active).

### Git (required)

The flow uses plain `git` for branching, worktrees, and pushing. `/finishing-branch` is
**host-agnostic** — it pushes your branch and points you to your Git host's web UI to open a
Pull/Merge Request, so it works the same on GitHub, GitLab, or Bitbucket. No vendor CLI required.

### Node.js 18+ (optional — only for `/context7`)

The `/context7` skill fetches up-to-date library docs through Upstash's `ctx7` CLI, run on demand via
`npx -y ctx7 …` (no install or repo copy needed). It is **optional**: skip it and the skills fall back
to existing code patterns or `WebFetch`. For higher rate limits, set a free `CONTEXT7_API_KEY`
(from `context7.com/dashboard`) in your environment — never commit it.

## Installation

The framework is **not** self-contained inside `.claude/` — `.claude/CLAUDE.md` imports the policy
rules via `@../AGENTS.md`, and several skills read `spec-desc.md` and `specs/MANIFEST.md`. Copy the
following into the **root** of your existing project:

```bash
# Run from the root of your project. Adjust the source path to where you cloned accelerator-core.
SRC=path/to/accelerator-core

cp -r "$SRC/.claude"      ./.claude        # skills, agents, hooks, settings, CLAUDE.md
cp    "$SRC/AGENTS.md"    ./AGENTS.md       # REQUIRED — imported by .claude/CLAUDE.md (policy rules)
cp    "$SRC/spec-desc.md" ./spec-desc.md    # spec-structure template (architect / api-designer / docs-generator)
cp -r "$SRC/specs"        ./specs           # specs/MANIFEST.md — index of living specifications
cp -r "$SRC/tasks"        ./tasks           # tasks/.task-counter — task numbering

# If you copied via zip/sync (which can drop the executable bit), restore it:
chmod +x .claude/hooks/*.sh
```

Then **restart Claude Code** so the `SessionStart` hook runs and the `AGENTS.md` policy import loads.

> `tasks/` and `specs/` are re-created on demand when a skill first writes to them, but copying
> `tasks/.task-counter` and `specs/MANIFEST.md` keeps task numbering and the spec index consistent
> from the start.

**Verify the install:** start a session and run any skill (e.g. `/architect`). The SessionStart
hook should print a short project-context summary, and skills should respect the naming/quality
rules from `AGENTS.md`.

## Quick Start

Invoke a skill by typing `/` followed by its name:

```
/brainstorm user authentication with OAuth
```

Each skill prints a Context Summary when it finishes — paste it into the next one. For a full
end-to-end pass see [Working on a ticket](#working-on-a-ticket); for what every skill does, see the
[Skills](#skills) catalog.

### Working on a ticket

A typical end-to-end pass over a project ticket follows the flow below. You drive it — each skill
stops after its step and hands you a Context Summary you paste into the next one (no auto-chaining):

```
/requirements-analyst <paste the ticket / link / spec>   # decompose into actionable tasks
/brainstorm           # explore the design through dialogue
/architect            # confirm architecture & module placement
/api-designer         # design endpoints, schemas, OpenAPI (if the ticket has an API)
/writing-plans        # break the design into bite-sized implementation steps
/git-worktrees        # create an isolated workspace for the branch
/coder                # implement (handler → service → data access)
/code-reviewer        # review quality, security, performance
/test-generator       # add unit / integration tests (pytest)
/verify               # run the Definition of Done checklist (pytest, ruff, mypy)
/finishing-branch     # open a PR/MR or merge once everything is green
```

Skip steps that don't apply (e.g. `/api-designer` for a non-API change) and jump to `/debugger`
whenever something breaks. See [Skill Flow](#skill-flow) for the full map.

### Context Handoff

Each skill outputs a context summary. Pass it to the next skill:

```
/brainstorm Design user authentication feature

[skill completes and outputs a context summary]

/writing-plans Based on auth design: JWT with refresh tokens,
  endpoints for login/logout/refresh, dependency for protected routes
```

## Skill Flow

The ordering is shown in [Working on a ticket](#working-on-a-ticket). For the complete diagram —
including alternate paths and where `/debugger` fits — see `.claude/docs/SKILL-FLOW.md`.

## Skills

| Phase | Skill | Purpose |
| ----- | ----- | ------- |
| Understanding | `requirements-analyst` | Parse requirements, decompose into tasks |
| Understanding | `brainstorm` | Explore ideas through dialogue, create designs |
| Planning | `architect` | High-level architecture decisions |
| Planning | `api-designer` | REST API design, schemas, OpenAPI docs |
| Planning | `writing-plans` | Create granular implementation plans |
| Execution | `git-worktrees` | Create isolated git worktrees |
| Execution | `coder` | Implementation (handler → service → data access) |
| Quality | `code-reviewer` | Code quality, security, performance review |
| Quality | `test-generator` | Generate unit, integration, and E2E tests (pytest) |
| Quality | `debugger` | Root cause analysis and debugging |
| Quality | `verify` | Run DoD checklist, report pass/fail |
| Finalization | `finishing-branch` | Complete branch work, open PR/MR or merge |
| Finalization | `docs-generator` | Generate project documentation |
| Utility | `reflect` | Turn mistakes into permanent rules |
| Utility | `skill-creator` | Create new skills |
| Utility | `context7` | Fetch up-to-date library docs via the `ctx7` CLI (runs inline, no agent) |
| Utility | `gh-issue` | Create a GitHub issue in the project's unified style (runs inline, no agent) |
| Utility | `gh-pr` | Open a PR, keeping docs + `CHANGELOG.md` in sync (runs inline, no agent) |
| Utility | `gh-release` | Consolidate `[Unreleased]` → version, tag, publish the GitHub release (runs inline, no agent) |

## Agents

Each agent is the isolated execution environment its skill forks into (`context: fork`). Every agent:
1. Receives the SKILL.md content as its task (it does NOT re-invoke the skill — that would loop)
2. Executes the task completely in its own context
3. **STOPS** when done (no automatic chaining)
4. Outputs a **Context Summary** + **Next Steps**

The **user** decides the next step based on the suggestions. See `.claude/agents/README.md` for the full agent list.

## Hooks

| Hook                      | Trigger                  | Purpose                    |
| ------------------------- | ------------------------ | -------------------------- |
| `SessionStart`            | Session begins           | Python project context scanner |
| `PreToolUse:Write\|Edit`  | Before file modification | File naming validation     |
| `PreToolUse:Bash`         | Before bash command      | Destructive command guard  |
| `PostToolUse:Edit`        | After file edit          | Loop detection (7/10 edits)|

| Code | Meaning |
| ---- | --------------------------------|
| `0`  | Success, continue               |
| `1`  | Failure, but continue (warning) |
| `2`  | Failure, block operation        |

Hooks receive the tool-call JSON on **stdin**. See `.claude/hooks/README.md`.

## Settings

### Team Settings (`settings.json`)

Shared configuration committed to git: permissions (allows `uv`, `python`, `pytest`, `ruff`, `mypy`, `git`, etc., plus WebFetch to the Python/uv/pytest/GitHub docs domains) and hooks. Denies reading/writing `.env*` and secrets, and blocks destructive bash commands.

### Personal Settings (`settings.local.json`)

Personal overrides (gitignored) — custom notifications, preferences, experimental features.

## Creating New Skills

1. **Create skill directory**: `.claude/skills/<skill-name>/`
2. **Create SKILL.md** with frontmatter:

   ```markdown
   ---
   name: my-skill
   description: "What this skill does and when to trigger it"
   ---

   # Skill Name

   ## Overview
   ...

   ---

   ## Next Steps

   **Next by flow:** `/next-skill [context]` - Why use this.
   ```

3. **Create the matching agent** (autonomous skills only): `.claude/agents/<skill-name>-agent.md` (name it `<skill-name>`) and add `context: fork` + `agent: <skill-name>` to the skill's frontmatter, plus a `## Task` / `$ARGUMENTS` hook in its body. The agent is the skill's system-prompt wrapper and must not re-invoke the skill. **Skip this if the skill is interactive** (uses `AskUserQuestion`) — those run inline with no agent, since a fork can't dialogue with the user.

Use `/skill-creator` for guided skill creation.

## Best Practices

1. **Follow the flow** - use the suggested next steps
2. **Pass context** - include context summaries when invoking the next skill
3. **Use git worktrees** for isolated development
4. **Verify before claiming completion** - run `pytest`, `ruff`, `mypy`
5. **Review code** before merging
6. **Keep skills focused** - one skill, one purpose

## Trainer Platform API (TASK-001, Epic-01)

The first product slice built with this framework: the identity, tenancy and auth JSON API
(44 operations across `auth`, `me`, `trainer`, `public`, `admin`). The HTTP contract lives in
[specs/api-designer-spec.md](specs/api-designer-spec.md) and the design decisions in
[specs/architect-architecture.md](specs/architect-architecture.md); this section only covers running it.

```bash
docker compose up -d db redis          # PostgreSQL 16 + Redis 7
uv sync
uv run alembic upgrade head            # schema (extensions: citext, btree_gist, pg_trgm)
uv run uvicorn app.main:create_app --factory --reload   # http://localhost:8000/docs
uv run arq app.jobs.worker.WorkerSettings               # email / cleanup jobs + cron
```

Configuration comes from the environment or a local `.env` (never committed): `DATABASE_URL`,
`REDIS_URL`, `JWT_KEYS` (JSON map `kid -> secret`, at least 32 chars each), `JWT_CURRENT_KID`,
`IP_HASH_SALT`, `PUBLIC_BASE_URL`, `S3_BUCKET`, `MEDIA_BASE_URL` (optional). With `ENV=dev` missing
secrets are generated per process; any other `ENV` requires `JWT_KEYS` and `IP_HASH_SALT`.

Quality gates (the same commands as [.claude/DOD.md](.claude/DOD.md)):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest                          # unit + integration + e2e; needs the compose services
uv run pytest -m "not integration"     # unit tests only, no database or Redis
uv run pytest --cov=app --cov-report=term-missing
```

Integration and e2e tests run against the `trainer_test` database (the schema is rebuilt from the
Alembic migration once per session) and Redis db 15 (`TEST_REDIS_URL`). Load scenarios are in
`tests/load/`.

## Repository layout (reference)

The framework lives almost entirely under `.claude/`:

```
.claude/
├── skills/               # Skill implementations — the primary invocation surface (one dir each)
├── agents/               # Agent wrappers — isolated execution context for the autonomous skills
├── hooks/                # Shell hooks: session scan, file-naming, destructive-cmd guard, loop detection
├── docs/                 # Human navigation: SKILL-FLOW.md + moc-*.md (NOT loaded as skills)
├── CLAUDE.md             # Project memory; imports ../AGENTS.md
├── DOD.md                # Tiered Definition of Done checklist
├── GOLDEN-PRINCIPLES.md  # Python style invariants
├── STABILIZATION.md      # Error-to-rule process (used by /reflect)
├── settings.json         # Team config: permissions + hook wiring (committed)
└── settings.local.json   # Personal overrides (gitignored)
```

Plus the root files you copy alongside it: `AGENTS.md` (policy), `spec-desc.md` (spec template),
`specs/` (living specs + `MANIFEST.md`), and `tasks/` (`.task-counter` + per-task docs).

## References

- [Claude Code Documentation](https://docs.anthropic.com/claude-code)
- [.claude/agents/README.md](.claude/agents/README.md) - Agent architecture details
- [.claude/hooks/README.md](.claude/hooks/README.md) - Hook configuration guide
- [.claude/docs/SKILL-FLOW.md](.claude/docs/SKILL-FLOW.md) - Skill flow diagram
- [tasks/README.md](tasks/README.md) - Temporary per-feature task docs (delete after merge)
- [specs/README.md](specs/README.md) - Living specifications (kept; the project's durable memory)
