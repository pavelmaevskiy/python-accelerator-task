from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.identity import ShareLink

pytestmark = pytest.mark.integration

URL = "/api/v1/trainer/share-links"


async def test_create_player_static_link_has_no_expiry_and_no_max_uses(trainer_client) -> None:
    r = await trainer_client.post(URL, json={"kind": "player_static"})
    assert r.status_code == 201
    body = r.json()
    assert body["expires_at"] is None and body["max_uses"] is None and body["use_count"] == 0
    assert body["is_active"] is True and body["target_email"] is None
    assert body["url"] == f"http://testserver/join/{body['code']}"


async def test_create_coach_unique_requires_target_email_and_sets_7d_expiry_and_max_uses_1(
    trainer_client, fake_queue, clock
) -> None:
    r = await trainer_client.post(URL, json={"kind": "coach_unique", "target_email": "coach@x.co"})
    assert r.status_code == 201
    body = r.json()
    assert body["max_uses"] == 1 and body["target_email"] == "coach@x.co"
    from datetime import datetime

    assert datetime.fromisoformat(body["expires_at"]) == clock.now() + timedelta(days=7)
    [job] = fake_queue.named("send_coach_invite_email")
    assert job.kwargs == {"share_link_id": body["id"]}


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "coach_unique"},
        {"kind": "player_static", "target_email": "a@b.co"},
        {"kind": "other"},
        {"kind": "player_static", "expires_at": "2030-01-01T00:00:00Z"},
    ],
)
async def test_invalid_create_payloads_are_422(trainer_client, payload, fake_queue) -> None:
    r = await trainer_client.post(URL, json=payload)
    assert r.status_code == 422 and fake_queue.jobs == []


async def test_code_is_22_char_urlsafe(trainer_client) -> None:
    code = (await trainer_client.post(URL, json={"kind": "player_static"})).json()["code"]
    assert len(code) == 22 and set(code) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )


async def test_failed_create_enqueues_nothing(trainer_client, fake_queue) -> None:
    await trainer_client.post(URL, json={"kind": "coach_unique"})
    assert fake_queue.jobs == []


async def test_list_is_cursor_paged_and_filtered_by_kind_and_is_active(trainer_client) -> None:
    ids = []
    for _ in range(3):
        ids.append((await trainer_client.post(URL, json={"kind": "player_static"})).json()["id"])
    coach = (
        await trainer_client.post(URL, json={"kind": "coach_unique", "target_email": "c@x.co"})
    ).json()
    await trainer_client.delete(f"{URL}/{ids[0]}")

    page1 = (await trainer_client.get(URL, params={"limit": 2})).json()
    assert [i["id"] for i in page1["items"]] == [coach["id"], ids[2]]  # newest first
    assert page1["has_more"] is True and page1["next_cursor"]
    page2 = (
        await trainer_client.get(URL, params={"limit": 2, "cursor": page1["next_cursor"]})
    ).json()
    assert [i["id"] for i in page2["items"]] == [ids[1], ids[0]]
    assert page2["has_more"] is False and page2["next_cursor"] is None

    only_coach = (await trainer_client.get(URL, params={"kind": "coach_unique"})).json()
    assert [i["id"] for i in only_coach["items"]] == [coach["id"]]
    inactive = (await trainer_client.get(URL, params={"is_active": "false"})).json()
    assert [i["id"] for i in inactive["items"]] == [ids[0]]


async def test_list_rejects_bad_cursor_and_limit(trainer_client) -> None:
    bad_cursor = await trainer_client.get(URL, params={"cursor": "not-base64!!"})
    assert bad_cursor.status_code == 422 and bad_cursor.json()["error"]["code"] == "INVALID_CURSOR"
    assert (await trainer_client.get(URL, params={"limit": 0})).status_code == 422
    assert (await trainer_client.get(URL, params={"limit": 201})).status_code == 422


async def test_list_only_returns_own_links(trainer_client, make_trainer, client_for) -> None:
    other = client_for(await make_trainer())
    await other.post(URL, json={"kind": "player_static"})
    assert (await trainer_client.get(URL)).json()["items"] == []


async def test_delete_deactivates_idempotently_204(trainer_client, db_session) -> None:
    link_id = (await trainer_client.post(URL, json={"kind": "player_static"})).json()["id"]
    assert (await trainer_client.delete(f"{URL}/{link_id}")).status_code == 204
    assert (await trainer_client.delete(f"{URL}/{link_id}")).status_code == 204
    row = (await db_session.execute(select(ShareLink))).scalar_one()
    assert row.is_active is False


async def test_other_trainers_link_delete_is_404_not_403(
    trainer_client, make_trainer, client_for, db_session
) -> None:
    other = client_for(await make_trainer())
    link_id = (await other.post(URL, json={"kind": "player_static"})).json()["id"]
    r = await trainer_client.delete(f"{URL}/{link_id}")
    assert r.status_code == 404
    assert (await db_session.execute(select(ShareLink))).scalar_one().is_active is True


async def test_unknown_link_delete_is_404(trainer_client) -> None:
    r = await trainer_client.delete(f"{URL}/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_non_trainer_roles_get_403_on_all_trainer_share_link_routes(
    non_trainer_client,
) -> None:
    c = non_trainer_client
    zero = "00000000-0000-0000-0000-000000000000"
    assert (await c.get(URL)).status_code == 403
    assert (await c.post(URL, json={"kind": "player_static"})).status_code == 403
    assert (await c.delete(f"{URL}/{zero}")).status_code == 403


async def test_unauthenticated_is_401(client) -> None:
    assert (await client.get(URL)).status_code == 401
