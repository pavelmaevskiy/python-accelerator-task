from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.models.admin import AuditLog
from app.models.identity import ImpersonationSession, RefreshToken, User

pytestmark = pytest.mark.integration

URL = "/api/v1/admin/users"


async def test_list_filters_by_role_status_trainer_and_query(admin_client, seeded_users) -> None:
    s = seeded_users

    async def ids(**params):
        r = await admin_client.get(URL, params=params)
        assert r.status_code == 200, r.text
        return {i["id"] for i in r.json()["items"]}

    assert await ids(role="trainer") == {str(s["t1"].id), str(s["t2"].id)}
    assert await ids(status="inactive") == {str(s["inactive"].id)}
    assert await ids(trainer=str(s["t1"].id)) == {
        str(s["t1"].id), str(s["coach"].id), str(s["p1"].owner_user_id),
    }  # fmt: skip
    assert await ids(query="pia") == {str(s["p1"].owner_user_id)}  # profile name, case-insensitive
    assert await ids(query="coach1@") == {str(s["coach"].id)}  # email
    assert await ids(role="player", status="active") == {
        str(s["p1"].owner_user_id), str(s["p2"].owner_user_id),
    }  # fmt: skip
    assert await ids(query="no-such-person") == set()
    assert await ids(query="100%") == set()  # LIKE wildcards are escaped


async def test_list_row_shape_includes_tenant_info(admin_client, seeded_users) -> None:
    items = (await admin_client.get(URL, params={"role": "coach"})).json()["items"]
    [coach] = items
    assert coach["trainer_id"] == str(seeded_users["t1"].id) and coach["business_name"] == "Club A"
    assert coach["first_name"] == "Carl" and "password_hash" not in coach
    trainer = (await admin_client.get(URL, params={"query": "tina"})).json()["items"][0]
    assert trainer["trainer_id"] == trainer["id"] and trainer["business_name"] == "Club A"


async def test_list_cursor_pagination_has_more_and_next_cursor_and_bad_cursor_422(
    admin_client, seeded_users
) -> None:
    # newest first; the calling admin (created after the seed data) is the newest row
    order = [str(admin_client.user.id)] + [str(i) for i in reversed(seeded_users["order"])]
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params = {"limit": 3, **({"cursor": cursor} if cursor else {})}
        page = (await admin_client.get(URL, params=params)).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not page["has_more"]:
            assert cursor is None
            break
    assert seen == order
    bad = await admin_client.get(URL, params={"cursor": "###"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_CURSOR"
    assert (await admin_client.get(URL, params={"limit": 201})).status_code == 422
    assert (await admin_client.get(URL, params={"query": "a"})).status_code == 422
    assert (await admin_client.get(URL, params={"role": "wizard"})).status_code == 422


async def test_list_sorted_created_at_desc_id_desc(admin_client, seeded_users, db_session) -> None:
    same = (await db_session.execute(select(User.created_at).limit(1))).scalar_one()
    await db_session.execute(update(User).values(created_at=same))  # force ties
    await db_session.commit()
    items = (await admin_client.get(URL)).json()["items"]
    ids = [i["id"] for i in items]
    assert ids == sorted(ids, reverse=True)
    first = (await admin_client.get(URL, params={"limit": 3})).json()
    second = (
        await admin_client.get(URL, params={"limit": 50, "cursor": first["next_cursor"]})
    ).json()
    assert [i["id"] for i in first["items"]] + [i["id"] for i in second["items"]] == ids


async def test_admin_routes_403_for_an_impersonated_principal(
    make_user, auth, client, db_session, clock
) -> None:
    # Only reachable with a forged/legacy session row: impersonating an admin is refused by the
    # service, but the guard must still hold on every admin route.
    target = await make_user(role="super_admin")
    admin = await make_user(role="super_admin")
    jti = str(uuid4())
    db_session.add(
        ImpersonationSession(
            admin_user_id=admin.id,
            target_user_id=target.id,
            expires_at=clock.now() + timedelta(minutes=30),
            jti=jti,
        )
    )
    await db_session.commit()
    headers = auth(target, act=admin.id, jti=jti)
    for method, path in (
        ("GET", URL),
        ("GET", f"{URL}/{target.id}"),
        ("POST", f"{URL}/{target.id}/deactivate"),
    ):
        r = await client.request(method, path, headers=headers)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "ADMIN_FORBIDDEN_WHILE_IMPERSONATING"


async def test_all_admin_routes_403_for_every_non_admin_role(
    non_trainer_client, trainer_client, child_client, seeded_users
) -> None:
    some = str(seeded_users["t1"].id)
    for c in (non_trainer_client, trainer_client, child_client):
        if c.user.role.value == "super_admin":
            continue
        calls = [
            c.get(URL), c.get(f"{URL}/{some}"), c.patch(f"{URL}/{some}", json={}),
            c.post(f"{URL}/{some}/deactivate"), c.post(f"{URL}/{some}/reactivate"),
        ]  # fmt: skip
        for call in calls:
            r = await call
            assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


async def test_admin_routes_require_authentication(client) -> None:
    assert (await client.get(URL)).status_code == 401


async def test_get_user_and_unknown_user_404(admin_client, seeded_users) -> None:
    r = await admin_client.get(f"{URL}/{seeded_users['p1'].owner_user_id}")
    assert r.status_code == 200 and r.json()["first_name"] == "Pia"
    assert (await admin_client.get(f"{URL}/{uuid4()}")).status_code == 404
    assert (await admin_client.get(f"{URL}/not-a-uuid")).status_code == 422


async def test_patch_user_updates_allowed_fields_and_rejects_role_status_422(
    admin_client, make_user
) -> None:
    user = await make_user(verified=False)
    r = await admin_client.patch(
        f"{URL}/{user.id}",
        json={"first_name": "Zed", "phone": "+15551234567", "is_email_verified": True,
              "must_change_password": True},
    )  # fmt: skip
    body = r.json()
    assert r.status_code == 200 and body["first_name"] == "Zed" and body["phone"] == "+15551234567"
    assert body["is_email_verified"] is True and body["must_change_password"] is True
    for forbidden in ({"role": "super_admin"}, {"status": "active"}, {"email": "x@y.co"},
                      {"phone": "123"}):  # fmt: skip
        assert (await admin_client.patch(f"{URL}/{user.id}", json=forbidden)).status_code == 422
    cleared = await admin_client.patch(f"{URL}/{user.id}", json={"phone": None})
    assert cleared.json()["phone"] is None


async def test_patch_erased_user_409_USER_ERASED(admin_client, make_user) -> None:
    erased = await make_user(status="deleted")
    r = await admin_client.patch(f"{URL}/{erased.id}", json={"first_name": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "USER_ERASED"
    assert (await admin_client.patch(f"{URL}/{uuid4()}", json={})).status_code == 404


async def test_deactivate_is_idempotent_revokes_refresh_and_blocks_next_request(
    admin_client, login_as, make_user, client, db_session
) -> None:
    user = await make_user()
    session = await login_as(user)
    assert (await client.get("/api/v1/me", headers=session.headers)).status_code == 200

    r = await admin_client.post(f"{URL}/{user.id}/deactivate")
    assert r.status_code == 200 and r.json()["status"] == "inactive"
    again = await admin_client.post(f"{URL}/{user.id}/deactivate")
    assert again.status_code == 200 and again.json()["status"] == "inactive"

    blocked = await client.get("/api/v1/me", headers=session.headers)  # same access token
    assert blocked.status_code == 401
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": session.refresh_token}
    )
    assert refresh.status_code == 401
    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user.id)))
        .scalars()
        .all()
    )
    assert rows and all(row.revoked_at is not None for row in rows)
    login = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "correct horse battery staple"}
    )
    assert login.status_code == 401


async def test_reactivate_restores_active_but_not_deleted_409_USER_ERASED(
    admin_client, make_user, client
) -> None:
    user = await make_user()
    await admin_client.post(f"{URL}/{user.id}/deactivate")
    r = await admin_client.post(f"{URL}/{user.id}/reactivate")
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert (await admin_client.post(f"{URL}/{user.id}/reactivate")).status_code == 200  # idempotent
    login = await client.post(
        "/api/v1/auth/login", json={"email": user.email, "password": "correct horse battery staple"}
    )
    assert login.status_code == 200
    erased = await make_user(status="deleted")
    for action in ("deactivate", "reactivate"):
        e = await admin_client.post(f"{URL}/{erased.id}/{action}")
        assert e.status_code == 409 and e.json()["error"]["code"] == "USER_ERASED"
    assert (await admin_client.post(f"{URL}/{uuid4()}/deactivate")).status_code == 404


async def test_every_mutation_writes_an_audit_row(
    admin_client, make_user, db_session, audit_rows
) -> None:
    user = await make_user()
    await admin_client.patch(
        f"{URL}/{user.id}", json={"first_name": "Zed", "phone": "+15551234567"}
    )
    await admin_client.post(f"{URL}/{user.id}/deactivate")
    await admin_client.post(f"{URL}/{user.id}/reactivate")
    assert await audit_rows(action="user.updated") == 1
    assert await audit_rows(action="user.deactivated") == 1
    assert await audit_rows(action="user.reactivated") == 1
    row = (
        await db_session.execute(select(AuditLog).where(AuditLog.action == "user.updated"))
    ).scalar_one()
    assert row.actor_user_id == admin_client.user.id and row.target_id == user.id
    assert row.details == {"fields": ["first_name", "phone"]}  # names only, no PII values
    await admin_client.post(f"{URL}/{user.id}/reactivate")  # no-op: no extra row
    assert await audit_rows(action="user.reactivated") == 1


async def test_failed_mutation_writes_no_audit_row(admin_client, make_user, audit_rows) -> None:
    erased = await make_user(status="deleted")
    await admin_client.patch(f"{URL}/{erased.id}", json={"first_name": "x"})
    assert await audit_rows(action="user.updated") == 0
