import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.models.identity import (
    CoachMembership,
    ImpersonationSession,
    OneTimeToken,
    PlayerProfile,
    Profile,
    PurchaseApproval,
    RefreshToken,
    ShareLink,
    TrainerProfile,
    User,
    UserErasure,
)

pytestmark = pytest.mark.integration


def url(user_id: object) -> str:
    return f"/api/v1/admin/users/{user_id}"


REASON = {"reason": "GDPR art. 17 request"}


async def reload(db_session, model, pk):  # noqa: ANN001
    return await db_session.get(model, pk, populate_existing=True)


async def test_erase_anonymises_in_place_and_never_deletes_user_row(
    admin_client, db_session, victim
) -> None:
    r = await admin_client.delete(url(victim.id), json=REASON)
    assert r.status_code == 204 and r.content == b""
    user = await reload(db_session, User, victim.id)
    assert user is not None  # the row is never deleted
    assert user.email == f"erased+{victim.id}@invalid" and user.status.value == "deleted"
    assert user.deleted_at is not None and user.is_email_verified is False
    assert user.password_hash.startswith("$argon2id$")
    profile = await reload(db_session, Profile, victim.id)
    assert profile.first_name == "Deleted User" and profile.last_name == ""
    assert profile.phone is None and profile.school is None and profile.avatar_key is None
    own = await reload(db_session, PlayerProfile, victim.own_profile.id)
    assert own.display_name == "Deleted User" and own.birth_date is None and own.is_active is False


async def test_erased_user_shows_up_in_admin_directory_as_deleted(admin_client, victim) -> None:
    await admin_client.delete(url(victim.id), json=REASON)
    shown = (await admin_client.get(url(victim.id))).json()
    assert shown["status"] == "deleted" and shown["email"].startswith("erased+")
    assert shown["deleted_at"] is not None and shown["first_name"] == "Deleted User"


async def test_erase_revokes_refresh_families_consumes_one_time_tokens_ends_impersonation_sessions(
    admin_client, login_as, victim, issue_token, client, db_session
) -> None:
    first, second = await login_as(victim), await login_as(victim)
    reset_token = await issue_token(victim, "password_reset")
    await admin_client.post(f"{url(victim.id)}/impersonate")
    await admin_client.delete(url(victim.id), json=REASON)

    for s in (first, second):
        r = await client.post("/api/v1/auth/refresh", json={"refresh_token": s.refresh_token})
        assert r.status_code == 401
    rows = (
        (await db_session.execute(select(RefreshToken).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    assert rows and all(row.revoked_at is not None for row in rows)
    tokens = (
        (await db_session.execute(select(OneTimeToken).execution_options(populate_existing=True)))
        .scalars()
        .all()
    )
    assert tokens and all(t.consumed_at is not None for t in tokens)
    confirm = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={"token": reset_token, "new_password": "tr1cky-Horse-staple-9"},
    )
    assert confirm.status_code == 422  # token consumed
    sessions = (
        (
            await db_session.execute(
                select(ImpersonationSession).execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    assert sessions and all(s.ended_at is not None for s in sessions)
    assert (await client.get("/api/v1/me", headers=first.headers)).status_code == 401


async def test_erase_writes_user_erasures_row_with_email_hash_and_no_fk_to_user(
    admin_client, victim, db_session
) -> None:
    await admin_client.delete(
        url(victim.id), json={"reason": "retention case", "legal_retention_until": "2033-01-01"}
    )
    row = (await db_session.execute(select(UserErasure))).scalar_one()
    assert row.original_user_id == victim.id
    assert row.original_email_hash == hashlib.sha256(b"vic.tim9137@x.co").hexdigest()
    assert row.erased_by_user_id == admin_client.user.id and row.reason == "retention case"
    assert row.legal_retention_until.isoformat() == "2033-01-01"
    fks = (
        (
            await db_session.execute(
                text(
                    "select kcu.column_name from information_schema.table_constraints tc "
                    "join information_schema.key_column_usage kcu using (constraint_name, table_schema) "
                    "where tc.constraint_type='FOREIGN KEY' and tc.table_name='user_erasures'"
                )
            )
        )
        .scalars()
        .all()
    )
    assert fks == ["erased_by_user_id"]  # the ledger survives the user: original_user_id has no FK


async def test_erasing_a_parent_erases_owned_dependents_and_their_logins_in_same_transaction(
    admin_client, victim, make_dependent, make_child, db_session
) -> None:
    kid = await make_dependent(victim, display_name="Jinxie Quillfeather")
    child_user, child_profile = await make_child(victim, email="jinxie.child9137@x.co")
    r = await admin_client.delete(url(victim.id), json=REASON)
    assert r.status_code == 204
    for pid in (kid.id, child_profile.id):
        p = await reload(db_session, PlayerProfile, pid)
        assert p.display_name == "Deleted User" and p.is_active is False
        assert p.birth_date.isoformat() == "1900-01-02"
    child = await reload(db_session, User, child_user.id)
    assert child.status.value == "deleted" and child.email == f"erased+{child.id}@invalid"
    assert (await reload(db_session, Profile, child_user.id)).first_name == "Deleted User"


async def test_active_commitments_409_via_checker_leaves_everything_untouched(
    admin_client, busy_checker, victim, db_session, fake_queue
) -> None:
    r = await admin_client.delete(url(victim.id), json=REASON)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTIVE_COMMITMENTS"
    assert r.json()["error"]["details"]["rsvp_ids"] == [str(i) for i in busy_checker.ids]
    user = await reload(db_session, User, victim.id)
    assert user.email == "vic.tim9137@x.co" and user.status.value == "active"
    assert (await reload(db_session, Profile, victim.id)).first_name == "Zorbaq"
    assert (await db_session.execute(select(UserErasure))).first() is None
    assert fake_queue.jobs == []


async def test_second_erase_is_409_ALREADY_ERASED_and_unknown_is_404(admin_client, victim) -> None:
    assert (await admin_client.delete(url(victim.id), json=REASON)).status_code == 204
    again = await admin_client.delete(url(victim.id), json=REASON)
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_ERASED"
    assert (await admin_client.delete(url(uuid4()), json=REASON)).status_code == 404


@pytest.mark.parametrize("body", [{}, {"reason": "ab"}, {"reason": "x" * 501}, {"reason": "okay", "extra": 1},
                                  {"reason": "okay", "legal_retention_until": "soon"}])  # fmt: skip
async def test_body_requires_reason_min_3_chars(admin_client, victim, body) -> None:
    r = await admin_client.delete(url(victim.id), json=body)
    assert r.status_code == 422
    no_body = await admin_client.delete(url(victim.id))
    assert no_body.status_code == 422


async def test_s3_deletion_job_enqueued_after_commit_for_avatar_and_logo_keys(
    admin_client, victim, make_trainer, db_session, fake_queue
) -> None:
    from sqlalchemy import update

    trainer = await make_trainer()
    await db_session.execute(
        update(TrainerProfile)
        .where(TrainerProfile.user_id == trainer.id)
        .values(logo_key="logos/t/logo.png")
    )
    await db_session.commit()
    await admin_client.delete(url(trainer.id), json=REASON)
    await admin_client.delete(url(victim.id), json=REASON)
    keys = {j.kwargs["key"] for j in fake_queue.named("delete_s3_object")}
    assert keys == {"logos/t/logo.png", victim.avatar_key}


async def test_failed_erase_enqueues_no_s3_jobs(
    admin_client, busy_checker, victim, fake_queue
) -> None:
    await admin_client.delete(url(victim.id), json=REASON)
    assert fake_queue.named("delete_s3_object") == []


async def test_erased_user_cannot_log_in_or_reset_password(
    client, admin_client, victim, fake_queue
) -> None:
    from tests.conftest import TEST_PASSWORD

    await admin_client.delete(url(victim.id), json=REASON)
    login = await client.post(
        "/api/v1/auth/login", json={"email": "vic.tim9137@x.co", "password": TEST_PASSWORD}
    )
    assert login.status_code == 401
    fake_queue.jobs.clear()
    reset = await client.post(
        "/api/v1/auth/password-reset/request", json={"email": "vic.tim9137@x.co"}
    )
    assert reset.status_code == 202 and fake_queue.jobs == []


async def test_erased_trainer_loses_public_presence_and_links(
    admin_client, make_trainer, make_share_link, redis, client, db_session
) -> None:
    trainer = await make_trainer(slug="soon-gone", business_name="Soon Gone")
    link = await make_share_link(trainer=trainer, kind="coach_unique", target_email="someone@x.co")
    await client.get("/api/v1/public/trainers/soon-gone/branding")
    assert await redis.exists("public:branding:soon-gone") == 1
    assert (await admin_client.delete(url(trainer.id), json=REASON)).status_code == 204
    assert await redis.exists("public:branding:soon-gone") == 0
    assert (await client.get("/api/v1/public/trainers/soon-gone/branding")).status_code == 404
    tp = await reload(db_session, TrainerProfile, trainer.id)
    assert tp.slug == f"erased-{trainer.id.hex}" and tp.business_name == "Deleted Business"
    stored = await reload(db_session, ShareLink, link.id)
    assert stored.is_active is False and stored.target_email is None
    assert (await client.get(f"/api/v1/public/share-links/{link.code}")).status_code == 404


async def test_erased_coach_is_removed_from_the_tenant_and_invite_email_is_scrubbed(
    admin_client, make_trainer, make_coach, make_share_link, db_session
) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer=trainer, email="coach.erase@x.co")
    link = await make_share_link(
        trainer=trainer, kind="coach_unique", target_email="coach.erase@x.co"
    )
    assert (await admin_client.delete(url(coach.id), json=REASON)).status_code == 204
    m = (
        await db_session.execute(select(CoachMembership).execution_options(populate_existing=True))
    ).scalar_one()
    assert m.is_active is False and m.left_at is not None
    assert (await reload(db_session, ShareLink, link.id)).target_email is None


async def test_erasing_a_parent_expires_pending_approvals_and_scrubs_notes(
    admin_client, victim, make_dependent, make_trainer, make_approval, db_session
) -> None:
    kid = await make_dependent(victim)
    approval = await make_approval(parent=victim, player=kid, trainer=await make_trainer())
    approval.parent_note = "call me on 555-9137"
    await db_session.commit()
    await admin_client.delete(url(victim.id), json=REASON)
    row = await reload(db_session, PurchaseApproval, approval.id)
    assert row.parent_note is None and row.status.value == "expired"


async def test_erase_writes_an_audit_row_without_pii(
    admin_client, victim, db_session, audit_rows
) -> None:
    from app.models.admin import AuditLog

    await admin_client.delete(url(victim.id), json=REASON)
    assert await audit_rows(action="user.erased") == 1
    row = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "user.erased"))
    ).scalar_one()
    assert row.actor_user_id == admin_client.user.id and row.target_id == victim.id
    assert row.details is None


async def test_non_admins_and_impersonated_admins_cannot_erase(
    non_trainer_client, trainer_client, victim
) -> None:
    for c in (non_trainer_client, trainer_client):
        if c.user.role.value != "super_admin":
            assert (await c.delete(url(victim.id), json=REASON)).status_code == 403


async def test_erasing_a_child_login_anonymises_its_profile_and_cuts_trainer_links(
    admin_client, victim, make_child, make_trainer, link_player, db_session
) -> None:
    from app.models.identity import TrainerPlayerLink

    trainer = await make_trainer()
    child_user, child_profile = await make_child(victim, email="kid.login4411@x.co")
    child_profile.school = "Zephyr Junior"
    child_profile.jersey_number = 7
    child_profile.emergency_contact_name = "Aunt Quill"
    link = await link_player(trainer, child_profile)
    await db_session.commit()

    r = await admin_client.delete(url(child_user.id), json=REASON)
    assert r.status_code == 204

    profile = await reload(db_session, PlayerProfile, child_profile.id)
    assert profile.display_name == "Deleted User" and profile.is_active is False
    assert profile.birth_date.isoformat() == "1900-01-02"
    assert profile.school is None and profile.jersey_number is None
    assert profile.emergency_contact_name is None
    assert (await reload(db_session, TrainerPlayerLink, link.id)).status.value == "inactive"
    parent = await reload(db_session, User, victim.id)  # the parent is untouched
    assert parent.status.value == "active" and parent.email == "vic.tim9137@x.co"


async def test_erasing_a_parent_cuts_trainer_links_of_dependents(
    admin_client, victim, make_dependent, make_trainer, link_player, db_session
) -> None:
    from app.models.identity import TrainerPlayerLink

    kid = await make_dependent(victim)
    link = await link_player(await make_trainer(), kid)
    assert (await admin_client.delete(url(victim.id), json=REASON)).status_code == 204
    assert (await reload(db_session, TrainerPlayerLink, link.id)).status.value == "inactive"


async def test_cannot_erase_self_409_or_another_super_admin_403(
    admin_client, make_user, db_session
) -> None:
    own = await admin_client.delete(url(admin_client.user.id), json=REASON)
    assert own.status_code == 409 and own.json()["error"]["code"] == "CANNOT_TARGET_SELF"
    peer = await make_user(role="super_admin")
    r = await admin_client.delete(url(peer.id), json=REASON)
    assert r.status_code == 403 and r.json()["error"]["code"] == "CANNOT_MODIFY_ADMIN"
    for u in (admin_client.user, peer):
        assert (await reload(db_session, User, u.id)).status.value == "active"
    assert (await db_session.execute(select(UserErasure))).first() is None
