"""The admin directory must stay index-driven at 10k users (p95 < 500 ms target)."""

import json
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, text

from app.repositories.user_repo import UserFilter, UserRepo

pytestmark = pytest.mark.integration


async def _seed(db_session, n: int = 10_000) -> None:
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, email, password_hash, role, status, is_email_verified,
                               must_change_password, created_at, updated_at)
            SELECT gen_random_uuid(), 'user' || g || '@bulk.example', 'x',
                   (ARRAY['player','trainer','coach'])[1 + g % 3]::user_role, 'active', true, false,
                   now() - (g || ' seconds')::interval, now()
            FROM generate_series(1, :n) g
            """
        ),
        {"n": n},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO profiles (user_id, first_name, last_name, timezone, created_at, updated_at)
            SELECT id, 'First' || substr(email, 5, 6), 'Last' || substr(md5(email), 1, 6), 'UTC',
                   now(), now() FROM users
            """
        )
    )
    await db_session.commit()
    await db_session.execute(text("ANALYZE users"))
    await db_session.execute(text("ANALYZE profiles"))
    await db_session.commit()


async def _explain(db_session, stmt) -> tuple[str, float]:
    """Capture the statement SQLAlchemy would run, then EXPLAIN it with the same parameters."""
    captured: list[tuple[str, tuple]] = []

    def grab(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            captured.append((statement, parameters))

    sync_engine = db_session.bind.sync_engine
    event.listen(sync_engine, "before_cursor_execute", grab)
    try:
        started = time.perf_counter()
        await db_session.execute(stmt)
        elapsed = time.perf_counter() - started
    finally:
        event.remove(sync_engine, "before_cursor_execute", grab)
    statement, params = captured[-1]
    conn = await db_session.connection()
    raw = await conn.exec_driver_sql("EXPLAIN (FORMAT JSON) " + statement, params)
    plan = raw.scalar_one()
    return json.dumps(plan), elapsed


async def test_list_over_10k_users_uses_index_scan_not_seq_scan(db_session) -> None:
    await _seed(db_session)
    stmt = UserRepo.list_stmt(UserFilter(), None, 50)
    plan, elapsed = await _explain(db_session, stmt)
    assert "ix_users_created_id" in plan, plan
    assert (
        '"Node Type": "Seq Scan", "Parallel Aware": false, "Async Capable": false, "Relation Name": "users"'
        not in plan
    )
    assert elapsed < 0.5

    cursor = (datetime.now(UTC), __import__("uuid").uuid4())
    page2_plan, elapsed2 = await _explain(db_session, UserRepo.list_stmt(UserFilter(), cursor, 50))
    assert "ix_users_created_id" in page2_plan and elapsed2 < 0.5


async def test_name_and_email_search_is_fast_and_matches_the_trigram_indexes(db_session) -> None:
    await _seed(db_session)
    stmt = UserRepo.list_stmt(UserFilter(query="user777"), None, 50)
    _, elapsed = await _explain(db_session, stmt)
    assert elapsed < 0.5
    rows = await UserRepo(db_session).list_page(UserFilter(query="user777"), None, 50)
    assert rows and all(
        "user777" in r.user.email or "user777" in r.profile.first_name.lower() for r in rows
    )

    # At 10k rows the planner rightly prefers a seq scan; forbid it to prove the predicate
    # expressions line up with the GIN trigram indexes created by the migration.
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    plan, _ = await _explain(db_session, stmt)
    assert "ix_users_email_trgm" in plan, plan
    assert "ix_profiles_first_name_trgm" in plan or "ix_profiles_last_name_trgm" in plan, plan
