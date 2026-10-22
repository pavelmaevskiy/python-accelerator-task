import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

pytestmark = pytest.mark.integration


async def test_all_epic01_tables_exist(db_session) -> None:
    rows = await db_session.execute(
        text("select tablename from pg_tables where schemaname='public'")
    )
    names = {r[0] for r in rows}
    assert {
        "users",
        "profiles",
        "trainer_profiles",
        "coach_profiles",
        "coach_memberships",
        "player_profiles",
        "trainer_player_links",
        "share_links",
        "share_link_events",
        "availability_windows",
        "purchase_approvals",
        "impersonation_sessions",
        "user_erasures",
        "refresh_tokens",
        "one_time_tokens",
        "audit_log",
        "feature_flags",
        "idempotency_keys",
    } <= names


async def test_coach_can_have_only_one_active_membership(
    db_session, make_user, make_trainer
) -> None:
    coach = await make_user(role="coach")
    t1, t2 = await make_trainer(), await make_trainer()
    sql = text(
        "insert into coach_memberships (id, coach_user_id, trainer_id, is_active) "
        "values (gen_random_uuid(), :c, :t, true)"
    )
    await db_session.execute(sql, {"c": coach.id, "t": t1.id})
    with pytest.raises(IntegrityError):
        await db_session.execute(sql, {"c": coach.id, "t": t2.id})


async def test_overlapping_availability_rejected_but_adjacent_allowed(
    db_session, make_player
) -> None:
    p = await make_player()
    ins = text(
        "insert into availability_windows (id, player_profile_id, weekday, start_time, end_time, "
        "is_available) values (gen_random_uuid(), :p, 1, :s, :e, true)"
    )
    from datetime import time

    await db_session.execute(ins, {"p": p.id, "s": time(16), "e": time(18)})
    await db_session.execute(ins, {"p": p.id, "s": time(18), "e": time(20)})  # half-open: allowed
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.execute(ins, {"p": p.id, "s": time(17), "e": time(19)})


async def test_availability_requires_exactly_one_subject(db_session) -> None:
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "insert into availability_windows (id, weekday, start_time, end_time, is_available) "
                "values (gen_random_uuid(), 1, '10:00', '11:00', true)"
            )
        )


async def test_users_email_is_case_insensitive_unique(db_session, make_user) -> None:
    await make_user(email="A@b.co")
    with pytest.raises(IntegrityError):
        await make_user(email="a@B.co")


async def test_birth_date_before_1900_is_rejected(db_session, make_user) -> None:
    from datetime import date

    from app.models.identity import PlayerProfile

    u = await make_user()
    db_session.add(
        PlayerProfile(
            owner_user_id=u.id, display_name="x", birth_date=date(1899, 12, 31), gender="other"
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_share_link_kind_rules_are_checked(db_session, make_trainer) -> None:
    t = await make_trainer()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "insert into share_links (id, code, trainer_id, created_by_user_id, kind, max_uses) "
                "values (gen_random_uuid(), 'c1', :t, :t, 'player_static', 5)"
            ),
            {"t": t.id},
        )
