"""After erasure the original PII must appear in no table (the ledger keeps only a SHA-256)."""

from datetime import timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

NEEDLES = [
    "vic.tim9137",  # email local part
    "Zorbaq",  # first name
    "Quillfeather",  # last name (also in the dependent's display name)
    "5559137001",  # phone
    "Zephyr",  # school
    "Jinxie",  # dependent's name
    "jinxie.child9137",  # child login email
    "newaddr9137",  # pending email-change target
    "555-9137",  # parent's approval note
    "asked 9137",  # impersonation reason
]


async def _scan(db_session, needle: str) -> list[str]:
    columns = (
        await db_session.execute(
            text(
                "select table_name, column_name from information_schema.columns "
                "where table_schema='public' and table_name <> 'alembic_version' and "
                "(data_type in ('text','character varying','character','json','jsonb') "
                " or (data_type = 'USER-DEFINED' and udt_name = 'citext'))"
            )
        )
    ).all()
    hits: list[str] = []
    for table, column in columns:
        n = (
            await db_session.execute(
                text(f'select count(*) from "{table}" where "{column}"::text ilike :p'),  # noqa: S608
                {"p": f"%{needle}%"},
            )
        ).scalar_one()
        if n:
            hits.append(f"{table}.{column}")
    return hits


async def test_original_email_appears_nowhere_in_any_table_or_index(
    admin_client,
    victim,
    make_dependent,
    make_child,
    make_trainer,
    make_approval,
    make_share_link,
    issue_token,
    db_session,
) -> None:
    kid = await make_dependent(victim, display_name="Jinxie Quillfeather")
    await make_child(victim, email="jinxie.child9137@x.co", first_name="Jinxie")
    trainer = await make_trainer()
    approval = await make_approval(parent=victim, player=kid, trainer=trainer)
    approval.parent_note = "call me on 555-9137"
    await make_share_link(trainer=trainer, kind="coach_unique", target_email="vic.tim9137@x.co")
    await issue_token(
        victim, "email_change", ttl=timedelta(hours=24), payload={"new_email": "newaddr9137@x.co"}
    )
    await admin_client.post(
        f"/api/v1/admin/users/{victim.id}/impersonate",
        json={"reason": "customer zorbaq asked 9137"},
    )
    await db_session.commit()
    before = {needle: await _scan(db_session, needle) for needle in NEEDLES}
    assert all(before.values()), before  # the scan really finds the PII while it exists

    r = await admin_client.request(
        "DELETE", f"/api/v1/admin/users/{victim.id}", json={"reason": "gdpr request"}
    )
    assert r.status_code == 204
    await db_session.rollback()
    after = {needle: await _scan(db_session, needle) for needle in NEEDLES}
    assert after == {needle: [] for needle in NEEDLES}, after
    ledger = (
        await db_session.execute(text("select original_email_hash from user_erasures"))
    ).scalar_one()
    assert len(ledger) == 64 and "vic" not in ledger
