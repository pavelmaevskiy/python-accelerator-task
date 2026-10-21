import asyncio

import pytest
from sqlalchemy import select

from app.models.identity import RefreshToken

pytestmark = pytest.mark.integration

REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"


async def test_refresh_rotates_and_old_token_becomes_consumed(
    client, logged_in, db_session
) -> None:
    r = await client.post(REFRESH, json={"refresh_token": logged_in.refresh_token})
    assert r.status_code == 200
    body = r.json()
    assert body["refresh_token"] != logged_in.refresh_token
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    rows = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(rows) == 2
    assert len({r.family_id for r in rows}) == 1  # same family
    assert sum(r.consumed_at is not None for r in rows) == 1


async def test_new_access_token_from_refresh_is_valid(client, logged_in, app) -> None:
    from app.api.deps import PrincipalDep

    @app.get("/whoami")
    async def whoami(p: PrincipalDep) -> dict[str, str]:
        return {"id": str(p.user_id)}

    body = (await client.post(REFRESH, json={"refresh_token": logged_in.refresh_token})).json()
    r = await client.get("/whoami", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert r.status_code == 200


async def test_reusing_consumed_token_revokes_family_and_writes_audit(
    client, logged_in, audit_rows
) -> None:
    first = (await client.post(REFRESH, json={"refresh_token": logged_in.refresh_token})).json()
    r = await client.post(REFRESH, json={"refresh_token": logged_in.refresh_token})
    assert r.status_code == 401 and r.json()["error"]["code"] == "REFRESH_REUSE_DETECTED"
    # the successor is now dead too
    r2 = await client.post(REFRESH, json={"refresh_token": first["refresh_token"]})
    assert r2.status_code == 401 and r2.json()["error"]["code"] == "REFRESH_INVALID"
    assert await audit_rows(action="refresh_reuse") == 1  # survives the failed request


async def test_unknown_refresh_token_is_REFRESH_INVALID(client) -> None:
    r = await client.post(REFRESH, json={"refresh_token": "x" * 43})
    assert r.status_code == 401 and r.json()["error"]["code"] == "REFRESH_INVALID"


async def test_expired_refresh_token_is_REFRESH_INVALID(client, logged_in, clock) -> None:
    clock.advance(days=31)
    r = await client.post(REFRESH, json={"refresh_token": logged_in.refresh_token})
    assert r.status_code == 401 and r.json()["error"]["code"] == "REFRESH_INVALID"


async def test_short_refresh_token_is_422(client) -> None:
    assert (await client.post(REFRESH, json={"refresh_token": "short"})).status_code == 422


async def test_refresh_for_deactivated_user_is_401(client, logged_in, deactivate) -> None:
    await deactivate(logged_in.user)
    r = await client.post(REFRESH, json={"refresh_token": logged_in.refresh_token})
    assert r.status_code == 401


async def test_logout_revokes_family_and_is_idempotent(client, logged_in) -> None:
    h = {"Authorization": f"Bearer {logged_in.access_token}"}
    body = {"refresh_token": logged_in.refresh_token}
    first = await client.post(LOGOUT, json=body, headers=h)
    assert first.status_code == 204 and first.content == b""
    assert (await client.post(LOGOUT, json=body, headers=h)).status_code == 204
    assert (await client.post(REFRESH, json=body)).status_code == 401


async def test_logout_with_unknown_token_is_204(client, logged_in) -> None:
    r = await client.post(LOGOUT, json={"refresh_token": "nope"}, headers=logged_in.headers)
    assert r.status_code == 204


async def test_logout_cannot_revoke_another_users_family(
    client, logged_in, make_user, login_as
) -> None:
    other = await login_as(await make_user())
    await client.post(
        LOGOUT, json={"refresh_token": other.refresh_token}, headers=logged_in.headers
    )
    r = await client.post(REFRESH, json={"refresh_token": other.refresh_token})
    assert r.status_code == 200


async def test_logout_without_access_token_is_401(client, logged_in) -> None:
    r = await client.post(LOGOUT, json={"refresh_token": logged_in.refresh_token})
    assert r.status_code == 401


async def test_ten_parallel_refreshes_of_one_token_yield_one_success(client, logged_in) -> None:
    body = {"refresh_token": logged_in.refresh_token}
    results = await asyncio.gather(*[client.post(REFRESH, json=body) for _ in range(10)])
    codes = sorted(r.status_code for r in results)
    assert codes == [200] + [401] * 9
