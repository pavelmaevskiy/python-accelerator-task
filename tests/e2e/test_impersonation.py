from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.admin import AuditLog
from app.models.identity import ImpersonationSession, RefreshToken

pytestmark = pytest.mark.integration


def start_url(user_id: object) -> str:
    return f"/api/v1/admin/users/{user_id}/impersonate"


EXIT = "/api/v1/auth/impersonation/exit"


async def test_impersonate_returns_token_with_act_claim_and_expiry_at_most_60_min(
    admin_client, make_user, container, clock
) -> None:
    from datetime import datetime

    target = await make_user(email="victim@x.co", first_name="Vic")
    r = await admin_client.post(start_url(target.id), json={"reason": "ticket 42"})
    assert r.status_code == 200
    body = r.json()
    claims = container.codec.decode_access(body["access_token"])
    assert claims["sub"] == str(target.id) and claims["act"] == str(admin_client.user.id)
    assert claims["role"] == "player" and claims["typ"] == "access"
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at - clock.now() <= timedelta(minutes=60)
    assert body["expires_in"] == 3600 and body["token_type"] == "bearer"
    assert body["target"]["id"] == str(target.id) and body["target"]["first_name"] == "Vic"


async def test_impersonation_never_issues_a_refresh_token(
    admin_client, make_user, client, db_session, clock
) -> None:
    target = await make_user()
    r = await admin_client.post(start_url(target.id))
    body = r.json()
    assert "refresh_token" not in body and "refresh" not in r.text
    assert (await db_session.execute(select(RefreshToken))).first() is None
    # the access token is the whole session: it cannot be exchanged for a longer one...
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": body["access_token"][:200]}
    )
    assert refresh.status_code == 401
    # ...and it dies at the 60-minute cap
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert (await client.get("/api/v1/me", headers=headers)).status_code == 200
    clock.advance(minutes=60, seconds=1)
    late = await client.get("/api/v1/me", headers=headers)
    assert late.status_code == 401 and late.json()["error"]["code"] == "IMPERSONATION_EXPIRED"


async def test_body_is_optional_and_reason_is_validated(
    admin_client, make_user, db_session
) -> None:
    target = await make_user()
    assert (await admin_client.post(start_url(target.id))).status_code == 200
    assert (
        await admin_client.post(start_url(target.id), json={"reason": "x" * 501})
    ).status_code == 422
    assert (
        await admin_client.post(start_url(target.id), json={"role": "admin"})
    ).status_code == 422
    row = (await db_session.execute(select(ImpersonationSession))).scalars().first()
    assert row.reason is None and row.admin_user_id == admin_client.user.id


async def test_impersonating_a_super_admin_is_403_CANNOT_IMPERSONATE_ADMIN(
    admin_client, make_user
) -> None:
    other_admin = await make_user(role="super_admin")
    r = await admin_client.post(start_url(other_admin.id))
    assert r.status_code == 403 and r.json()["error"]["code"] == "CANNOT_IMPERSONATE_ADMIN"
    own = await admin_client.post(start_url(admin_client.user.id))
    assert own.status_code == 403 and own.json()["error"]["code"] == "CANNOT_IMPERSONATE_ADMIN"


async def test_impersonating_while_impersonating_is_403(impersonated_client, make_user) -> None:
    other = await make_user()
    r = await impersonated_client.post(start_url(other.id))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ADMIN_FORBIDDEN_WHILE_IMPERSONATING"


@pytest.mark.parametrize(
    ("status", "code"),
    [("deleted", "USER_ERASED"), ("inactive", "USER_INACTIVE"), ("pending", "USER_INACTIVE")],
)
async def test_erased_or_inactive_target_is_409(admin_client, make_user, status, code) -> None:
    target = await make_user(status=status)
    r = await admin_client.post(start_url(target.id))
    assert r.status_code == 409 and r.json()["error"]["code"] == code
    assert (await admin_client.post(start_url(uuid4()))).status_code == 404


async def test_only_super_admins_can_impersonate(
    non_trainer_client, trainer_client, make_user
) -> None:
    target = await make_user()
    for c in (non_trainer_client, trainer_client):
        if c.user.role.value != "super_admin":
            assert (await c.post(start_url(target.id))).status_code == 403


async def test_every_impersonated_request_has_x_impersonated_by_header_and_an_audit_row(
    impersonated_client, audit_rows, db_session
) -> None:
    for _ in range(3):
        r = await impersonated_client.get("/api/v1/me")
        assert r.status_code == 200
        assert r.headers["X-Impersonated-By"] == str(impersonated_client.admin.id)
    assert await audit_rows(action="impersonated_request") == 3
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "impersonated_request")
            )
        )
        .scalars()
        .all()
    )
    assert all(
        row.impersonator_id == impersonated_client.admin.id
        and row.actor_user_id == impersonated_client.user.id
        and row.details == {"method": "GET", "path": "/api/v1/me"}
        for row in rows
    )
    assert await audit_rows(action="impersonation.started") == 1


async def test_failed_impersonated_request_still_leaves_its_audit_row(
    impersonated_client, audit_rows
) -> None:
    r = await impersonated_client.get("/api/v1/trainer/branding")  # 403 for a player
    assert r.status_code == 403
    assert await audit_rows(action="impersonated_request") == 1  # separate transaction


async def test_impersonated_principal_gets_403_on_all_admin_routes(
    impersonated_client, seeded_users
) -> None:
    some = seeded_users["t1"].id
    base = "/api/v1/admin/users"
    calls = [
        impersonated_client.get(base), impersonated_client.get(f"{base}/{some}"),
        impersonated_client.patch(f"{base}/{some}", json={}),
        impersonated_client.post(f"{base}/{some}/deactivate"),
        impersonated_client.post(f"{base}/{some}/reactivate"),
        impersonated_client.post(f"{base}/trainers", json={}),
        impersonated_client.post(start_url(some)),
    ]  # fmt: skip
    for call in calls:
        r = await call
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "ADMIN_FORBIDDEN_WHILE_IMPERSONATING"


async def test_impersonated_token_acts_with_target_permissions_only(
    admin_client, make_trainer, make_user, client
) -> None:
    trainer = await make_trainer()
    player = await make_user()
    t = (await admin_client.post(start_url(trainer.id))).json()["access_token"]
    p = (await admin_client.post(start_url(player.id))).json()["access_token"]
    as_trainer, as_player = ({"Authorization": f"Bearer {x}"} for x in (t, p))
    assert (await client.get("/api/v1/trainer/branding", headers=as_trainer)).status_code == 200
    assert (await client.get("/api/v1/me/availability", headers=as_trainer)).status_code == 403
    assert (await client.get("/api/v1/trainer/branding", headers=as_player)).status_code == 403
    me = (await client.get("/api/v1/me", headers=as_player)).json()
    assert me["id"] == str(player.id) and me["role"] == "player"


async def test_exit_ends_session_revokes_jti_and_token_is_then_401_IMPERSONATION_EXPIRED(
    impersonated_client, db_session, audit_rows
) -> None:
    r = await impersonated_client.post(EXIT)
    assert r.status_code == 204 and r.content == b""
    after = await impersonated_client.get("/api/v1/me")
    assert after.status_code == 401 and after.json()["error"]["code"] == "IMPERSONATION_EXPIRED"
    row = (await db_session.execute(select(ImpersonationSession))).scalar_one()
    assert row.ended_at is not None
    assert await audit_rows(action="impersonation.ended") == 1
    again = await impersonated_client.post(EXIT)
    assert again.status_code == 401


async def test_exit_with_a_normal_token_is_403_NOT_IMPERSONATING(player_client, client) -> None:
    r = await player_client.post(EXIT)
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_IMPERSONATING"
    assert (await client.post(EXIT)).status_code == 401


async def test_the_admins_own_session_is_unaffected_by_exit(
    impersonated_client, admin_client
) -> None:
    await impersonated_client.post(EXIT)
    assert (await admin_client.get("/api/v1/admin/users")).status_code == 200


async def test_expiry_cron_closes_sessions_past_expires_at_idempotently(
    admin_client, make_user, sessionmaker_, container, clock, db_session
) -> None:
    from app.jobs.identity_jobs import expire_impersonation_sessions

    old = await make_user()
    fresh = await make_user()
    await admin_client.post(start_url(old.id))
    clock.advance(minutes=45)
    await admin_client.post(start_url(fresh.id))
    clock.advance(minutes=20)  # first session is now 65 min old, the second 20
    ctx = {
        "sessionmaker": sessionmaker_,
        "clock": clock,
        "queue": container.queue,
        "codec": container.codec,
    }
    assert await expire_impersonation_sessions(ctx) == 1
    assert await expire_impersonation_sessions(ctx) == 0
    rows = {
        r.target_user_id: r.ended_at
        for r in (
            await db_session.execute(
                select(ImpersonationSession).execution_options(populate_existing=True)
            )
        ).scalars()
    }
    assert rows[old.id] is not None and rows[fresh.id] is None


async def test_impersonation_token_dies_when_the_impersonating_admin_is_deactivated(
    impersonated_client, deactivate
) -> None:
    assert (await impersonated_client.get("/api/v1/me")).status_code == 200
    await deactivate(impersonated_client.admin)
    r = await impersonated_client.get("/api/v1/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "IMPERSONATION_EXPIRED"
