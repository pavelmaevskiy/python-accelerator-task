# Project: Trainer Platform Backend

Multi-tenant JSON API (FastAPI + PostgreSQL) for sports trainers, their coaches, players and parents: identity & tenancy, events, CRM, content, payments/tokens, marketing, admin and public forms. Every trainer is a tenant; isolation is a security property. Normative conventions live in `Task/Epics/00_Backend_Conventions.md`.

## Specs Index

| File | Purpose | Depends On | Last Updated |
|------|---------|------------|--------------|
| architect-architecture.md | System design, components, data flow | - | 2026-10-21 (TASK-001) |
| api-designer-spec.md | Endpoints, schemas, authentication | architect-architecture | 2026-10-21 (TASK-001) |
| docs-generator-implementation.md | Build process, deployment, tooling | - | - |

## Key Decisions

- Architecture: single FastAPI service, layer-first `app/{api,services,repositories,models,jobs,core,db}`; route -> service -> repository; services own transactions
- Tenancy: `trainer_player_links` defines tenant membership; tenant-scoped repos require `trainer_id`; cross-tenant miss = 404
- Auth: JWT access (HS256 + kid, 15 min) + opaque rotating refresh (30 d, family reuse detection); argon2id; email verification is a hard login gate (TASK-001)
- API: `/api/v1`, error envelope `{error:{code,message,details}}`, cursor pagination `{items,next_cursor,has_more}`; `PUT` only for replace-set availability and presign; avatar/logo confirm via `avatar_key`/`logo_key` on PATCH; registration issues tokens only when mailbox control is proven (TASK-001)
- Data: UUIDv7 PKs, `ON DELETE RESTRICT`, GDPR erasure = anonymise in place
- Cross-epic seams: consumer-declared `Protocol`s (e.g. `ApprovalSubjectHandler`, erasure commitment check)
- Async: ARQ jobs enqueued post-commit; all email via jobs

## Tech Stack

Python 3.12 · uv · FastAPI · Pydantic v2 · SQLAlchemy 2.0 async (asyncpg) · Alembic · PostgreSQL 16 (citext, btree_gist, pg_trgm) · Redis 7 · ARQ · argon2-cffi · PyJWT · boto3/S3 · defusedxml · structlog · ruff · mypy · pytest

---

*This manifest is updated automatically by architect, api-designer, and docs-generator skills.*
*See `../spec-desc.md` for specification structure guidelines.*
