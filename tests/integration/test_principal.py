"""Principal resolution and guards, exercised through a tiny set of probe routes."""

from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import Depends
from sqlalchemy import select, update

from app.api.deps import PrincipalDep, reject_impersonation, require_role
from app.core.enums import UserRole
from app.models.admin import AuditLog
from app.models.identity import ImpersonationSession, User

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def probe_routes(app) -> None:
    @app.get("/whoami")
    async def whoami(p: PrincipalDep) -> dict[str, object]:
        return {
            "user_id": str(p.user_id),
            "role": p.role.value,
            "trainer_id": str(p.trainer_id) if p.trainer_id else None,
            "act": str(p.act) if p.act else None,
            "is_child": p.is_child,
        }

    @app.get("/trainer-only", dependencies=[Depends(require_role(UserRole.TRAINER))])
    async def trainer_only() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/no-impersonation", dependencies=[Depends(reject_impersonation)])
    async def no_impersonation() -> dict[str, bool]:
        return {"ok": True}


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer garbage"}, {"Authorization": "Basic abc"}],
)
async def test_missing_or_garbage_bearer_is_401(client, headers) -> None:
    r = await client.get("/whoami", headers=headers)
    assert r.status_code == 401
    assert r.json()["error"]["code"] in {"NOT_AUTHENTICATED", "INVALID_TOKEN"}


async def test_valid_token_resolves_principal_from_db_not_from_claims(
    client, make_user, auth
) -> None:
    user = await make_user(role="player")
    r = await client.get("/whoami", headers=auth(user))
    assert r.status_code == 200
    assert r.json()["role"] == "player" and r.json()["user_id"] == str(user.id)


async def test_deactivated_user_is_rejected_on_next_request(
    client, make_user, auth, db_session
) -> None:
    user = await make_user()
    headers = auth(user)
    assert (await client.get("/whoami", headers=headers)).status_code == 200
    await db_session.execute(update(User).where(User.id == user.id).values(status="inactive"))
    await db_session.commit()
    r = await client.get("/whoami", headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "ACCOUNT_INACTIVE"


async def test_require_role_returns_403_for_wrong_role(
    client, make_user, make_trainer, auth
) -> None:
    player = await make_user(role="player")
    trainer = await make_trainer()
    assert (await client.get("/trainer-only", headers=auth(player))).status_code == 403
    assert (await client.get("/trainer-only", headers=auth(trainer))).status_code == 200


async def test_trainer_and_coach_get_their_tenant(client, make_trainer, make_coach, auth) -> None:
    trainer = await make_trainer()
    coach = await make_coach(trainer=trainer)
    assert (await client.get("/whoami", headers=auth(trainer))).json()["trainer_id"] == str(
        trainer.id
    )
    assert (await client.get("/whoami", headers=auth(coach))).json()["trainer_id"] == str(
        trainer.id
    )


async def test_act_claim_without_active_session_is_401_impersonation_expired(
    client, make_user, auth
) -> None:
    target = await make_user()
    admin = await make_user(role="super_admin")
    headers = auth(target, act=admin.id, jti=str(uuid4()))  # forged: no session row
    r = await client.get("/whoami", headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "IMPERSONATION_EXPIRED"


async def test_impersonated_request_sets_header_audits_and_is_blocked_from_admin_routes(
    client, make_user, auth, db_session, clock
) -> None:
    target = await make_user()
    admin = await make_user(role="super_admin")
    jti = str(uuid4())
    db_session.add(
        ImpersonationSession(
            admin_user_id=admin.id,
            target_user_id=target.id,
            expires_at=clock.now() + timedelta(minutes=60),
            jti=jti,
        )
    )
    await db_session.commit()
    headers = auth(target, act=admin.id, jti=jti)

    r = await client.get("/whoami", headers=headers)
    assert r.status_code == 200
    assert r.headers["X-Impersonated-By"] == str(admin.id)
    assert r.json()["act"] == str(admin.id)
    rows = (
        (
            await db_session.execute(
                select(AuditLog).where(AuditLog.action == "impersonated_request")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1 and rows[0].impersonator_id == admin.id
    assert rows[0].details == {"method": "GET", "path": "/whoami"}

    blocked = await client.get("/no-impersonation", headers=headers)
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "ADMIN_FORBIDDEN_WHILE_IMPERSONATING"

    clock.advance(minutes=61)  # session expired
    expired = await client.get("/whoami", headers=headers)
    assert expired.status_code == 401


async def test_x_trainer_context_for_player_must_match_active_link_else_404(
    client, make_player, make_trainer, link_player, auth, db_session
) -> None:
    profile = await make_player()
    linked, other = await make_trainer(), await make_trainer()
    await link_player(linked, profile)
    owner = await db_session.get(User, profile.owner_user_id)
    headers = auth(owner)

    ok = await client.get("/whoami", headers={**headers, "X-Trainer-Context": str(linked.id)})
    assert ok.status_code == 200 and ok.json()["trainer_id"] == str(linked.id)
    none = await client.get("/whoami", headers=headers)
    assert none.json()["trainer_id"] is None
    bad = await client.get("/whoami", headers={**headers, "X-Trainer-Context": str(other.id)})
    assert bad.status_code == 404
    invalid = await client.get("/whoami", headers={**headers, "X-Trainer-Context": "nope"})
    assert invalid.status_code == 422


async def test_inactive_link_does_not_grant_trainer_context(
    client, make_player, make_trainer, link_player, auth, db_session
) -> None:
    profile = await make_player()
    trainer = await make_trainer()
    await link_player(trainer, profile, status="inactive")
    owner = await db_session.get(User, profile.owner_user_id)
    r = await client.get("/whoami", headers={**auth(owner), "X-Trainer-Context": str(trainer.id)})
    assert r.status_code == 404
