import pytest
from sqlalchemy import select

from app.core.availability import Slot
from app.core.errors import DomainValidationError
from app.models.identity import AvailabilityWindow
from app.repositories.availability_repo import AvailabilityRepo

pytestmark = pytest.mark.integration

URL = "/api/v1/me/availability"


def win(weekday: int, start: str, end: str, available: bool = True) -> dict[str, object]:
    return {"weekday": weekday, "start_time": start, "end_time": end, "is_available": available}


async def test_put_replaces_whole_set_and_get_returns_it(player_client) -> None:
    first = await player_client.put(
        URL, json={"windows": [win(2, "09:00", "10:00"), win(1, "16:00", "18:00", False)]}
    )
    assert first.status_code == 200
    body = first.json()
    assert body["subject_type"] == "player" and body["subject_id"] == str(player_client.profile.id)
    assert [(w["weekday"], w["start_time"], w["is_available"]) for w in body["windows"]] == [
        (1, "16:00:00", False),
        (2, "09:00:00", True),
    ]
    await player_client.put(URL, json={"windows": [win(5, "07:00", "08:00")]})
    got = (await player_client.get(URL)).json()
    assert [(w["weekday"], w["start_time"]) for w in got["windows"]] == [(5, "07:00:00")]


async def test_put_empty_list_clears_everything(player_client) -> None:
    await player_client.put(URL, json={"windows": [win(1, "10:00", "11:00")]})
    r = await player_client.put(URL, json={"windows": []})
    assert r.status_code == 200 and r.json()["windows"] == []


async def test_put_overlapping_windows_is_422_OVERLAPPING_WINDOWS_and_set_unchanged(
    player_client,
) -> None:
    await player_client.put(URL, json={"windows": [win(1, "10:00", "11:00")]})
    r = await player_client.put(
        URL, json={"windows": [win(3, "16:00", "18:00"), win(3, "17:59", "18:30")]}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "OVERLAPPING_WINDOWS"
    kept = (await player_client.get(URL)).json()["windows"]
    assert [(w["weekday"], w["start_time"]) for w in kept] == [(1, "10:00:00")]


async def test_adjacent_windows_are_accepted(player_client) -> None:
    r = await player_client.put(
        URL, json={"windows": [win(1, "16:00", "18:00"), win(1, "18:00", "20:00")]}
    )
    assert r.status_code == 200 and len(r.json()["windows"]) == 2


async def test_same_hours_on_different_weekdays_are_accepted(player_client) -> None:
    r = await player_client.put(
        URL, json={"windows": [win(1, "16:00", "18:00"), win(2, "16:00", "18:00")]}
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "windows",
    [
        [win(1, "18:00", "16:00")],
        [win(1, "16:00", "16:00")],
        [win(7, "10:00", "11:00")],
        [{"weekday": 1, "start_time": "10:00", "end_time": "11:00", "extra": 1}],
    ],
)
async def test_invalid_windows_are_422(player_client, windows) -> None:
    assert (await player_client.put(URL, json={"windows": windows})).status_code == 422


async def test_more_than_100_windows_is_422(player_client) -> None:
    windows = [win(d, f"{h:02d}:00", f"{h:02d}:30") for d in range(7) for h in range(15)]
    assert len(windows) == 105
    assert (await player_client.put(URL, json={"windows": windows})).status_code == 422
    ok = await player_client.put(URL, json={"windows": windows[:100]})
    assert ok.status_code == 200


async def test_coach_has_own_availability_trainer_and_admin_get_403(
    coach_client, trainer_client, admin_client
) -> None:
    put = await coach_client.put(URL, json={"windows": [win(1, "10:00", "11:00")]})
    assert put.status_code == 200
    body = put.json()
    assert body["subject_type"] == "coach" and body["subject_id"] == str(coach_client.user.id)
    assert (await coach_client.get(URL)).json()["windows"][0]["weekday"] == 1
    for c in (trainer_client, admin_client):
        assert (await c.get(URL)).status_code == 403
        assert (await c.put(URL, json={"windows": []})).status_code == 403


async def test_child_login_may_set_own_availability(child_client, player_client) -> None:
    r = await child_client.put(URL, json={"windows": [win(1, "15:00", "16:00")]})
    assert r.status_code == 200
    assert r.json()["subject_id"] == str(child_client.profile.id)  # the dependent's profile
    parent_view = (await player_client.get(URL)).json()
    assert parent_view["windows"] == []  # the parent's own set is separate


async def test_parent_sets_dependent_availability_and_other_parents_dependent_is_404(
    player_client, make_dependent, make_player, db_session
) -> None:
    kid = await make_dependent(player_client.user)
    stranger = await make_player()
    foreign_kid = await make_dependent(
        await db_session.get(type(player_client.user), stranger.owner_user_id)
    )
    mine = await player_client.put(
        f"/api/v1/me/players/{kid.id}/availability", json={"windows": [win(4, "10:00", "11:00")]}
    )
    assert mine.status_code == 200 and mine.json()["subject_id"] == str(kid.id)
    other = await player_client.put(
        f"/api/v1/me/players/{foreign_kid.id}/availability", json={"windows": []}
    )
    assert other.status_code == 404


async def test_child_cannot_set_dependent_availability_and_non_players_get_403(
    child_client, coach_client, make_dependent, player_client
) -> None:
    kid = await make_dependent(player_client.user)
    path = f"/api/v1/me/players/{kid.id}/availability"
    for c in (child_client, coach_client):
        assert (await c.put(path, json={"windows": []})).status_code == 403


async def test_trainer_reads_only_actively_linked_players_other_tenant_is_404(
    trainer_client, make_player, make_trainer, link_player, client_for, db_session
) -> None:
    mine, foreign = await make_player(), await make_player()
    other_trainer = await make_trainer()
    await link_player(trainer_client.user, mine)
    await link_player(other_trainer, foreign)
    from app.models.identity import User

    owner = client_for(await db_session.get(User, mine.owner_user_id))
    await owner.put(URL, json={"windows": [win(1, "10:00", "11:00")]})

    ok = await trainer_client.get(f"/api/v1/trainer/players/{mine.id}/availability")
    assert ok.status_code == 200 and ok.json()["windows"][0]["weekday"] == 1
    cross = await trainer_client.get(f"/api/v1/trainer/players/{foreign.id}/availability")
    assert cross.status_code == 404
    unknown = await trainer_client.get(
        "/api/v1/trainer/players/00000000-0000-0000-0000-000000000000/availability"
    )
    assert unknown.status_code == 404


async def test_trainer_loses_access_when_link_is_deactivated(
    trainer_client, make_player, link_player
) -> None:
    p = await make_player()
    await link_player(trainer_client.user, p, status="inactive")
    r = await trainer_client.get(f"/api/v1/trainer/players/{p.id}/availability")
    assert r.status_code == 404


async def test_other_roles_get_403_on_trainer_availability_route(
    non_trainer_client, make_player
) -> None:
    p = await make_player()
    r = await non_trainer_client.get(f"/api/v1/trainer/players/{p.id}/availability")
    assert r.status_code == 403


async def test_db_exclusion_constraint_backstops_a_race(db_session, make_player) -> None:
    from datetime import time

    p = await make_player()
    repo = AvailabilityRepo(db_session)
    with pytest.raises(DomainValidationError) as e:
        await repo.replace_for_player(
            p.id,
            [(Slot(1, time(16), time(18)), True), (Slot(1, time(17), time(19)), True)],
        )  # bypasses the Python check, as a concurrent writer would
    assert e.value.code == "OVERLAPPING_WINDOWS"
    await db_session.rollback()
    assert (await db_session.execute(select(AvailabilityWindow))).first() is None


async def test_availability_requires_authentication(client) -> None:
    assert (await client.get(URL)).status_code == 401
