"""Cross-tenant access returns 404, never 403 (a 403 would confirm the row exists). NFR-004."""

from uuid import uuid4

import pytest

from app.models.identity import PlayerProfile, User

pytestmark = pytest.mark.integration

ME = "/api/v1/me"


@pytest.fixture
async def world(
    player_client, trainer_client, make_trainer, make_player, make_dependent, make_approval,
    make_share_link, link_player, db_session, client_for,
):  # fmt: skip
    """Two tenants: the caller's (trainer_client / player_client) and a foreign one."""
    other_trainer = await make_trainer()
    other_profile = await make_player()
    other_parent = await db_session.get(User, other_profile.owner_user_id)
    foreign_dependent = await make_dependent(other_parent)
    await link_player(other_trainer, foreign_dependent)
    await link_player(other_trainer, other_profile)
    foreign_approval = await make_approval(
        parent=other_parent, player=foreign_dependent, trainer=other_trainer
    )
    foreign_link = await make_share_link(trainer=other_trainer)
    return {
        "other_trainer": other_trainer,
        "foreign_player": other_profile,
        "foreign_dependent": foreign_dependent,
        "foreign_approval": foreign_approval,
        "foreign_link": foreign_link,
    }


def routes(world):  # noqa: ANN001, ANN201
    dep, appr = world["foreign_dependent"].id, world["foreign_approval"].id
    return [
        ("trainer", "DELETE", f"/api/v1/trainer/share-links/{world['foreign_link'].id}", None),
        (
            "trainer",
            "GET",
            f"/api/v1/trainer/players/{world['foreign_player'].id}/availability",
            None,
        ),
        ("trainer", "GET", f"/api/v1/trainer/players/{dep}/availability", None),
        ("player", "GET", f"{ME}/players/{dep}", None),
        ("player", "PATCH", f"{ME}/players/{dep}", {"school": "x"}),
        ("player", "DELETE", f"{ME}/players/{dep}", None),
        ("player", "POST", f"{ME}/players/{dep}/trainers", {"trainer_id": str(uuid4())}),
        ("player", "DELETE", f"{ME}/players/{dep}/trainers/{world['other_trainer'].id}", None),
        ("player", "PUT", f"{ME}/players/{dep}/availability", {"windows": []}),
        ("player", "POST", f"{ME}/approvals/{appr}/approve", None),
        ("player", "POST", f"{ME}/approvals/{appr}/deny", None),
    ]


async def test_cross_tenant_access_is_404_on_every_id_addressed_route(
    world, trainer_client, player_client
) -> None:
    clients = {"trainer": trainer_client, "player": player_client}
    failures: list[str] = []
    for who, method, path, body in routes(world):
        kwargs = (
            {"headers": {"Idempotency-Key": "isolation-key-0001"}} if "approvals" in path else {}
        )
        if body is not None:
            kwargs["json"] = body
        r = await clients[who].request(method, path, **kwargs)
        if r.status_code != 404:
            failures.append(f"{who} {method} {path}: {r.status_code} {r.text[:80]}")
    assert not failures, "\n".join(failures)


async def test_foreign_rows_are_untouched_after_the_attempts(
    world, trainer_client, player_client, db_session
) -> None:
    from app.models.identity import PurchaseApproval, ShareLink

    await trainer_client.delete(f"/api/v1/trainer/share-links/{world['foreign_link'].id}")
    await player_client.delete(f"{ME}/players/{world['foreign_dependent'].id}")
    await player_client.post(
        f"{ME}/approvals/{world['foreign_approval'].id}/deny",
        headers={"Idempotency-Key": "iso-key-0001"},
    )
    link = await db_session.get(ShareLink, world["foreign_link"].id, populate_existing=True)
    dep = await db_session.get(PlayerProfile, world["foreign_dependent"].id, populate_existing=True)
    approval = await db_session.get(
        PurchaseApproval, world["foreign_approval"].id, populate_existing=True
    )
    assert link.is_active and dep.is_active and approval.status.value == "pending"


async def test_unknown_ids_and_foreign_ids_are_indistinguishable(
    world, trainer_client, player_client
) -> None:
    foreign = await player_client.get(f"{ME}/players/{world['foreign_dependent'].id}")
    unknown = await player_client.get(f"{ME}/players/{uuid4()}")
    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json() == unknown.json()


async def test_player_with_mismatched_x_trainer_context_is_404_on_every_player_scoped_route(
    world, player_client
) -> None:
    headers = {"X-Trainer-Context": str(world["other_trainer"].id)}  # not linked to this player
    for method, path in [
        ("GET", ME),
        ("PATCH", ME),
        ("GET", f"{ME}/contexts"),
        ("GET", f"{ME}/availability"),
        ("GET", f"{ME}/players"),
        ("GET", f"{ME}/approvals"),
    ]:
        r = await player_client.request(
            method, path, headers=headers, json={} if method == "PATCH" else None
        )
        assert r.status_code == 404, (method, path, r.status_code)


async def test_matching_x_trainer_context_is_accepted(
    world, player_client, trainer_client, link_player, db_session
) -> None:
    await link_player(trainer_client.user, player_client.profile)
    r = await player_client.get(ME, headers={"X-Trainer-Context": str(trainer_client.user.id)})
    assert r.status_code == 200 and r.json()["trainer_id"] == str(trainer_client.user.id)


async def test_body_trainer_id_is_ignored_or_rejected_never_trusted(
    world, trainer_client, make_trainer, db_session
) -> None:
    from sqlalchemy import select

    from app.models.identity import ShareLink

    other = world["other_trainer"]
    r = await trainer_client.post(
        "/api/v1/trainer/share-links", json={"kind": "player_static", "trainer_id": str(other.id)}
    )
    assert r.status_code == 422  # extra field forbidden
    ok = await trainer_client.post("/api/v1/trainer/share-links", json={"kind": "player_static"})
    link = (
        await db_session.execute(select(ShareLink).where(ShareLink.code == ok.json()["code"]))
    ).scalar_one()
    assert link.trainer_id == trainer_client.user.id  # derived from the token only
    patch = await trainer_client.patch(
        "/api/v1/trainer/branding", json={"trainer_id": str(other.id)}
    )
    assert patch.status_code == 422
    assert (await trainer_client.get(f"/api/v1/trainer/branding?trainer_id={other.id}")).json()[
        "business_name"
    ] == "Acme Tennis"  # query param ignored: still the caller's own branding


async def test_trainer_listing_only_ever_contains_own_tenant(world, trainer_client) -> None:
    mine = await trainer_client.post("/api/v1/trainer/share-links", json={"kind": "player_static"})
    items = (await trainer_client.get("/api/v1/trainer/share-links")).json()["items"]
    assert [i["id"] for i in items] == [mine.json()["id"]]
    assert str(world["foreign_link"].id) not in str(items)


async def test_deactivated_link_removes_tenant_access_immediately(
    trainer_client, player_client, make_dependent, link_player
) -> None:
    kid = await make_dependent(player_client.user)
    await link_player(trainer_client.user, player_client.profile)  # parent is a customer
    await player_client.post(
        f"{ME}/players/{kid.id}/trainers", json={"trainer_id": str(trainer_client.user.id)}
    )
    url = f"/api/v1/trainer/players/{kid.id}/availability"
    assert (await trainer_client.get(url)).status_code == 200
    removed = await player_client.delete(f"{ME}/players/{kid.id}/trainers/{trainer_client.user.id}")
    assert removed.status_code == 204
    assert (await trainer_client.get(url)).status_code == 404  # no cache, no grace period


async def test_deactivated_link_also_removes_the_player_context(
    trainer_client, player_client, link_player, db_session
) -> None:
    from sqlalchemy import update

    from app.models.identity import TrainerPlayerLink

    await link_player(trainer_client.user, player_client.profile)
    headers = {"X-Trainer-Context": str(trainer_client.user.id)}
    assert (await player_client.get(ME, headers=headers)).status_code == 200
    await db_session.execute(update(TrainerPlayerLink).values(status="inactive"))
    await db_session.commit()
    assert (await player_client.get(ME, headers=headers)).status_code == 404


async def test_coach_sees_only_the_tenant_of_its_active_membership(
    make_coach, make_trainer, client_for
) -> None:
    mine = await make_trainer()
    coach = await make_coach(trainer=mine)
    r = await client_for(coach).get(ME)
    assert r.json()["trainer_id"] == str(mine.id)
    assert r.json()["coach_profile"]["trainer_id"] == str(mine.id)
