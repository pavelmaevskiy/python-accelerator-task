import pytest
from sqlalchemy import select

from app.models.identity import PlayerProfile, TrainerPlayerLink, User

pytestmark = pytest.mark.integration

PLAYERS = "/api/v1/me/players"
CONTEXTS = "/api/v1/me/contexts"


def kid(**kw: object) -> dict[str, object]:
    return {"display_name": "Sam", "birth_date": "2015-03-04", "gender": "male", **kw}


# --- contexts ---------------------------------------------------------------------------------


async def test_contexts_lists_only_active_links_with_branding(
    player_client, make_trainer, link_player
) -> None:
    active = await make_trainer(business_name="Active Club")
    inactive = await make_trainer(business_name="Gone Club")
    await link_player(active, player_client.profile)
    await link_player(inactive, player_client.profile, status="inactive")
    r = await player_client.get(CONTEXTS)
    assert r.status_code == 200
    [item] = r.json()["items"]
    assert item["trainer_id"] == str(active.id) and item["business_name"] == "Active Club"
    assert item["player_profile_id"] == str(player_client.profile.id)
    assert {"slug", "logo_url", "brand_primary_color", "linked_at"} <= set(item)


async def test_contexts_include_dependents_links_and_child_sees_only_its_own(
    player_client, child_client, make_trainer, link_player
) -> None:
    t1, t2 = await make_trainer(), await make_trainer()
    await link_player(t1, player_client.profile)
    await link_player(t2, child_client.profile)
    parent = {i["trainer_id"] for i in (await player_client.get(CONTEXTS)).json()["items"]}
    child = {i["trainer_id"] for i in (await child_client.get(CONTEXTS)).json()["items"]}
    assert parent == {str(t1.id), str(t2.id)} and child == {str(t2.id)}


async def test_contexts_403_for_non_player(trainer_client, coach_client, admin_client) -> None:
    for c in (trainer_client, coach_client, admin_client):
        assert (await c.get(CONTEXTS)).status_code == 403


# --- create -----------------------------------------------------------------------------------


async def test_create_dependent_201_links_requested_trainers_only_if_parent_is_linked(
    player_client, make_trainer, link_player, db_session
) -> None:
    trainer = await make_trainer(business_name="Club")
    await link_player(trainer, player_client.profile)
    r = await player_client.post(PLAYERS, json=kid(trainer_ids=[str(trainer.id)]))
    assert r.status_code == 201
    body = r.json()
    assert body["is_dependent"] is True and body["has_login"] is False
    assert body["token_spend_requires_approval"] is True and body["skill_level"] is None
    assert [(t["trainer_id"], t["status"]) for t in body["trainers"]] == [
        (str(trainer.id), "active")
    ]
    link = (
        (
            await db_session.execute(
                select(TrainerPlayerLink).where(TrainerPlayerLink.player_profile_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    assert {lk.source.value for lk in link if str(lk.player_profile_id) == body["id"]} == {"admin"}


async def test_dependent_inherits_share_link_source_from_parents_link(
    player_client, make_share_link, db_session
) -> None:
    link = await make_share_link()
    await player_client.post(
        "/api/v1/auth/register",
        params={"share_code": link.code},
        json={
            "email": "x@x.co",
            "password": "tr1cky-Horse-staple-9",
            "first_name": "A",
            "last_name": "B",
        },
    )  # authenticated redeem: parent links via share link
    r = await player_client.post(PLAYERS, json=kid(trainer_ids=[str(link.trainer_id)]))
    assert r.status_code == 201
    child_link = (
        await db_session.execute(
            select(TrainerPlayerLink).where(TrainerPlayerLink.player_profile_id == r.json()["id"])
        )
    ).scalar_one()
    assert child_link.source.value == "share_link" and child_link.share_link_id == link.id


async def test_trainer_id_not_linked_to_parent_is_404(
    player_client, make_trainer, db_count
) -> None:
    foreign = await make_trainer()
    before = await db_count(PlayerProfile)
    r = await player_client.post(PLAYERS, json=kid(trainer_ids=[str(foreign.id)]))
    assert r.status_code == 404
    assert await db_count(PlayerProfile) == before  # nothing half-created


async def test_inactive_parent_link_does_not_count_as_linked(
    player_client, make_trainer, link_player
) -> None:
    trainer = await make_trainer()
    await link_player(trainer, player_client.profile, status="inactive")
    r = await player_client.post(PLAYERS, json=kid(trainer_ids=[str(trainer.id)]))
    assert r.status_code == 404


async def test_duplicate_dependent_409_unless_allow_duplicate_true(player_client) -> None:
    assert (await player_client.post(PLAYERS, json=kid())).status_code == 201
    dup = await player_client.post(PLAYERS, json=kid(display_name="SAM"))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "DUPLICATE_PLAYER"
    ok = await player_client.post(PLAYERS, params={"allow_duplicate": "true"}, json=kid())
    assert ok.status_code == 201
    other_day = await player_client.post(PLAYERS, json=kid(birth_date="2015-03-05"))
    assert other_day.status_code == 201


async def test_child_cannot_create_dependent_403_CHILD_CANNOT_CREATE_DEPENDENT(
    child_client,
) -> None:
    r = await child_client.post(PLAYERS, json=kid())
    assert r.status_code == 403 and r.json()["error"]["code"] == "CHILD_CANNOT_CREATE_DEPENDENT"


@pytest.mark.parametrize(
    ("birth", "status"),
    [("2025-10-21", 201), ("2007-10-21", 422), ("2000-01-01", 422), ("2999-01-01", 422)],
)
async def test_create_age_bound_is_1_to_18(player_client, clock, birth, status) -> None:
    # Pin "today" so boundary dates are deterministic regardless of the real date.
    from datetime import UTC, datetime

    clock._at = datetime(2026, 10, 21, 9, tzinfo=UTC)
    r = await player_client.post(PLAYERS, json=kid(birth_date=birth))
    assert r.status_code == status
    if status == 422:
        assert r.json()["error"]["code"] == "INVALID_AGE"


@pytest.mark.parametrize(
    "payload",
    [
        {"display_name": ""},
        {"gender": "robot"},
        {"jersey_number": 1000},
        {"emergency_contact_phone": "123"},
        {"skill_level": "advanced"},
        {"birth_date": "1800-01-01"},
    ],
)
async def test_create_validation_errors_are_422(player_client, payload) -> None:
    assert (await player_client.post(PLAYERS, json=kid(**payload))).status_code == 422


async def test_create_requires_required_fields(player_client) -> None:
    for missing in ("display_name", "birth_date", "gender"):
        body = kid()
        del body[missing]
        assert (await player_client.post(PLAYERS, json=body)).status_code == 422


# --- list / get / patch -----------------------------------------------------------------------


async def test_list_players_is_cursor_paged_and_owner_scoped(
    player_client, make_player, make_dependent, db_session
) -> None:
    ids = []
    for i in range(3):
        ids.append((await player_client.post(PLAYERS, json=kid(display_name=f"K{i}"))).json()["id"])
    stranger = await make_player()
    await make_dependent(await db_session.get(User, stranger.owner_user_id))
    p1 = (await player_client.get(PLAYERS, params={"limit": 2})).json()
    assert [i["id"] for i in p1["items"]] == [ids[2], ids[1]] and p1["has_more"] is True
    p2 = (await player_client.get(PLAYERS, params={"limit": 2, "cursor": p1["next_cursor"]})).json()
    assert [i["id"] for i in p2["items"]] == [ids[0]] and p2["has_more"] is False


async def test_list_excludes_own_adult_profile(player_client) -> None:
    items = (await player_client.get(PLAYERS)).json()["items"]
    assert items == []  # the parent's own profile is not a dependent


async def test_child_gets_403_on_list(child_client) -> None:
    assert (await child_client.get(PLAYERS)).status_code == 403


async def test_get_patch_other_parents_dependent_is_404(
    player_client, make_player, make_dependent, db_session
) -> None:
    stranger = await make_player()
    foreign = await make_dependent(await db_session.get(User, stranger.owner_user_id))
    assert (await player_client.get(f"{PLAYERS}/{foreign.id}")).status_code == 404
    assert (
        await player_client.patch(f"{PLAYERS}/{foreign.id}", json={"school": "x"})
    ).status_code == 404
    assert (await player_client.delete(f"{PLAYERS}/{foreign.id}")).status_code == 404


async def test_get_own_adult_profile_id_is_404_because_it_is_not_a_dependent(player_client) -> None:
    assert (await player_client.get(f"{PLAYERS}/{player_client.profile.id}")).status_code == 404


async def test_patch_updates_fields_and_toggles_token_approval(player_client) -> None:
    pid = (await player_client.post(PLAYERS, json=kid())).json()["id"]
    r = await player_client.patch(
        f"{PLAYERS}/{pid}",
        json={"school": "PS 9", "jersey_number": 7, "token_spend_requires_approval": False},
    )
    body = r.json()
    assert r.status_code == 200 and body["school"] == "PS 9" and body["jersey_number"] == 7
    assert body["token_spend_requires_approval"] is False and body["display_name"] == "Sam"
    cleared = await player_client.patch(f"{PLAYERS}/{pid}", json={"school": None})
    assert cleared.json()["school"] is None


async def test_patch_birth_date_revalidates_age_422_INVALID_AGE(player_client) -> None:
    pid = (await player_client.post(PLAYERS, json=kid())).json()["id"]
    r = await player_client.patch(f"{PLAYERS}/{pid}", json={"birth_date": "1990-01-01"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_AGE"
    ok = await player_client.patch(f"{PLAYERS}/{pid}", json={"birth_date": "2016-01-01"})
    assert ok.status_code == 200 and ok.json()["birth_date"] == "2016-01-01"


async def test_patch_into_a_duplicate_is_409(player_client) -> None:
    await player_client.post(PLAYERS, json=kid(display_name="A"))
    pid = (await player_client.post(PLAYERS, json=kid(display_name="B"))).json()["id"]
    r = await player_client.patch(f"{PLAYERS}/{pid}", json={"display_name": "a"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_PLAYER"
    same = await player_client.patch(f"{PLAYERS}/{pid}", json={"display_name": "B"})
    assert same.status_code == 200  # renaming to itself is not a duplicate


async def test_patch_rejects_skill_level_field_422(player_client) -> None:
    pid = (await player_client.post(PLAYERS, json=kid())).json()["id"]
    r = await player_client.patch(f"{PLAYERS}/{pid}", json={"skill_level": "advanced"})
    assert r.status_code == 422


# --- delete -----------------------------------------------------------------------------------


async def test_delete_dependent_soft_deactivates_204_and_cuts_trainer_access(
    player_client, make_trainer, link_player, client_for, db_session
) -> None:
    trainer = await make_trainer()
    await link_player(trainer, player_client.profile)
    pid = (await player_client.post(PLAYERS, json=kid(trainer_ids=[str(trainer.id)]))).json()["id"]
    assert (await player_client.delete(f"{PLAYERS}/{pid}")).status_code == 204
    assert (await player_client.get(f"{PLAYERS}/{pid}")).status_code == 404
    row = await db_session.get(PlayerProfile, pid, populate_existing=True)
    assert row is not None and row.is_active is False  # never hard-deleted
    trainer_view = client_for(trainer)
    assert (
        await trainer_view.get(f"/api/v1/trainer/players/{pid}/availability")
    ).status_code == 404
    assert (await player_client.get(PLAYERS)).json()["items"] == []


async def test_delete_dependent_409_ACTIVE_COMMITMENTS_via_checker(
    player_client, busy_checker, db_session
) -> None:
    pid = (await player_client.post(PLAYERS, json=kid())).json()["id"]
    r = await player_client.delete(f"{PLAYERS}/{pid}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTIVE_COMMITMENTS"
    assert r.json()["error"]["details"]["rsvp_ids"] == [str(i) for i in busy_checker.ids]
    row = await db_session.get(PlayerProfile, pid, populate_existing=True)
    assert row.is_active is True


# --- trainer links ----------------------------------------------------------------------------


async def test_add_trainer_201_new_200_existing_404_unlinked(
    player_client, make_trainer, link_player
) -> None:
    mine, foreign = await make_trainer(), await make_trainer()
    await link_player(mine, player_client.profile)
    pid = (await player_client.post(PLAYERS, json=kid())).json()["id"]
    first = await player_client.post(f"{PLAYERS}/{pid}/trainers", json={"trainer_id": str(mine.id)})
    assert first.status_code == 201 and first.json()["status"] == "active"
    again = await player_client.post(f"{PLAYERS}/{pid}/trainers", json={"trainer_id": str(mine.id)})
    assert again.status_code == 200 and again.json()["trainer_id"] == str(mine.id)
    unlinked = await player_client.post(
        f"{PLAYERS}/{pid}/trainers", json={"trainer_id": str(foreign.id)}
    )
    assert unlinked.status_code == 404
    missing = await player_client.post(
        f"{PLAYERS}/00000000-0000-0000-0000-000000000000/trainers",
        json={"trainer_id": str(mine.id)},
    )
    assert missing.status_code == 404


async def test_remove_trainer_sets_link_inactive_204_and_readd_reactivates(
    player_client, make_trainer, link_player, db_count
) -> None:
    trainer = await make_trainer()
    await link_player(trainer, player_client.profile)
    pid = (await player_client.post(PLAYERS, json=kid(trainer_ids=[str(trainer.id)]))).json()["id"]
    url = f"{PLAYERS}/{pid}/trainers/{trainer.id}"
    assert (await player_client.delete(url)).status_code == 204
    assert (await player_client.delete(url)).status_code == 204  # idempotent
    shown = (await player_client.get(f"{PLAYERS}/{pid}")).json()["trainers"]
    assert [t["status"] for t in shown] == ["inactive"]  # history kept, never hard-deleted
    before = await db_count(TrainerPlayerLink)
    re = await player_client.post(f"{PLAYERS}/{pid}/trainers", json={"trainer_id": str(trainer.id)})
    assert re.status_code == 201 and await db_count(TrainerPlayerLink) == before  # no duplicate row


async def test_remove_unknown_link_is_404(player_client, make_trainer) -> None:
    pid = (await player_client.post(PLAYERS, json=kid())).json()["id"]
    other = await make_trainer()
    assert (await player_client.delete(f"{PLAYERS}/{pid}/trainers/{other.id}")).status_code == 404


async def test_remove_trainer_409_with_rsvp_ids_in_details(
    player_client, make_trainer, link_player, busy_checker
) -> None:
    trainer = await make_trainer()
    await link_player(trainer, player_client.profile)
    pid = (await player_client.post(PLAYERS, json=kid(trainer_ids=[str(trainer.id)]))).json()["id"]
    r = await player_client.delete(f"{PLAYERS}/{pid}/trainers/{trainer.id}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACTIVE_COMMITMENTS"
    assert r.json()["error"]["details"]["rsvp_ids"] == [str(i) for i in busy_checker.ids]
    assert busy_checker.calls[-1][1] == trainer.id  # checked per trainer
    still = (await player_client.get(f"{PLAYERS}/{pid}")).json()["trainers"]
    assert [t["status"] for t in still] == ["active"]


async def test_non_player_roles_get_403_on_players_routes(
    non_trainer_client, trainer_client
) -> None:
    for c in (non_trainer_client, trainer_client):
        if c.user.role.value == "player":
            continue
        assert (await c.get(PLAYERS)).status_code == 403
        assert (await c.post(PLAYERS, json=kid())).status_code == 403
