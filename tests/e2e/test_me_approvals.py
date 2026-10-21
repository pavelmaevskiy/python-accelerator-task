import pytest

from app.models.identity import PurchaseApproval

pytestmark = pytest.mark.integration

URL = "/api/v1/me/approvals"


def key(n: int = 1) -> dict[str, str]:
    return {"Idempotency-Key": f"test-key-{n:04d}"}


async def test_list_defaults_to_pending_and_paginates_and_is_parent_scoped(
    player_client, make_dependent, make_trainer, make_approval, make_player, db_session, client_for
) -> None:
    from app.models.identity import User

    kid = await make_dependent(player_client.user)
    trainer = await make_trainer(business_name="Club")
    mine = [
        await make_approval(parent=player_client.user, player=kid, trainer=trainer)
        for _ in range(3)
    ]
    await make_approval(parent=player_client.user, player=kid, trainer=trainer, status="denied")
    stranger = await make_player()
    other_parent = await db_session.get(User, stranger.owner_user_id)
    other_kid = await make_dependent(other_parent)
    await make_approval(parent=other_parent, player=other_kid, trainer=trainer)

    page1 = (await player_client.get(URL, params={"limit": 2})).json()
    assert [i["id"] for i in page1["items"]] == [str(mine[2].id), str(mine[1].id)]
    assert page1["has_more"] is True
    page2 = (
        await player_client.get(URL, params={"limit": 2, "cursor": page1["next_cursor"]})
    ).json()
    assert [i["id"] for i in page2["items"]] == [str(mine[0].id)] and page2["has_more"] is False
    item = page1["items"][0]
    assert item["status"] == "pending" and item["business_name"] == "Club"
    assert item["player_display_name"] == "Dependent" and item["currency"] == "usd"
    assert item["subject_type"] == "event_rsvp" and item["amount_cents"] == 2500
    denied = (await player_client.get(URL, params={"status": "denied"})).json()
    assert len(denied["items"]) == 1


async def test_approve_requires_idempotency_key_422(player_client, pending, handler_spy) -> None:
    r = await player_client.post(f"{URL}/{pending.id}/approve")
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    short = await player_client.post(
        f"{URL}/{pending.id}/approve", headers={"Idempotency-Key": "abc"}
    )
    assert short.status_code == 422
    assert handler_spy.approved == []


async def test_approve_calls_handler_and_returns_the_approval(
    player_client, pending, handler_spy, db_session, clock
) -> None:
    r = await player_client.post(
        f"{URL}/{pending.id}/approve", headers=key(), json={"parent_note": "go ahead"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "approved" and body["parent_note"] == "go ahead"
    assert body["responded_at"] is not None
    assert len(handler_spy.approved) == 1
    row = await db_session.get(PurchaseApproval, pending.id, populate_existing=True)
    assert row.status.value == "approved"


async def test_approve_works_without_a_body(player_client, pending, handler_spy) -> None:
    r = await player_client.post(f"{URL}/{pending.id}/approve", headers=key())
    assert r.status_code == 200 and r.json()["parent_note"] is None


async def test_approve_replay_with_same_key_returns_stored_response_without_second_handler_call(
    player_client, pending, handler_spy
) -> None:
    first = await player_client.post(f"{URL}/{pending.id}/approve", headers=key())
    second = await player_client.post(f"{URL}/{pending.id}/approve", headers=key())
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(handler_spy.approved) == 1


async def test_approve_non_pending_is_409_APPROVAL_NOT_PENDING(
    player_client, pending, handler_spy
) -> None:
    await player_client.post(f"{URL}/{pending.id}/approve", headers=key(1))
    r = await player_client.post(f"{URL}/{pending.id}/approve", headers=key(2))
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_NOT_PENDING"
    deny = await player_client.post(f"{URL}/{pending.id}/deny", headers=key(3))
    assert deny.status_code == 409


async def test_approve_expired_row_is_409(player_client, pending, handler_spy, clock) -> None:
    clock.advance(hours=48, seconds=1)
    r = await player_client.post(f"{URL}/{pending.id}/approve", headers=key())
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_NOT_PENDING"


async def test_unregistered_subject_is_409_unavailable_and_status_unchanged(
    player_client, make_dependent, make_trainer, make_approval, db_session
) -> None:
    kid = await make_dependent(player_client.user)
    approval = await make_approval(
        parent=player_client.user, player=kid, trainer=await make_trainer(), subject_type="content"
    )
    r = await player_client.post(f"{URL}/{approval.id}/approve", headers=key())
    assert r.status_code == 409 and r.json()["error"]["code"] == "APPROVAL_SUBJECT_UNAVAILABLE"
    row = await db_session.get(PurchaseApproval, approval.id, populate_existing=True)
    assert row.status.value == "pending"
    retry = await player_client.post(f"{URL}/{approval.id}/approve", headers=key())  # key reusable
    assert retry.status_code == 409


async def test_deny_sets_status_and_note_and_calls_handler(
    player_client, pending, handler_spy
) -> None:
    r = await player_client.post(
        f"{URL}/{pending.id}/deny", headers=key(), json={"parent_note": "not now"}
    )
    assert r.status_code == 200 and r.json()["status"] == "denied"
    assert r.json()["parent_note"] == "not now" and len(handler_spy.denied_or_expired) == 1


async def test_other_parents_approval_is_404(
    player_client, make_player, make_dependent, make_trainer, make_approval, db_session, handler_spy
) -> None:
    from app.models.identity import User

    stranger = await make_player()
    owner = await db_session.get(User, stranger.owner_user_id)
    approval = await make_approval(
        parent=owner, player=await make_dependent(owner), trainer=await make_trainer()
    )
    assert (
        await player_client.post(f"{URL}/{approval.id}/approve", headers=key())
    ).status_code == 404
    assert (await player_client.post(f"{URL}/{approval.id}/deny", headers=key())).status_code == 404
    assert handler_spy.approved == []


async def test_unknown_approval_is_404(player_client) -> None:
    r = await player_client.post(
        f"{URL}/00000000-0000-0000-0000-000000000000/approve", headers=key()
    )
    assert r.status_code == 404


async def test_parent_note_over_500_chars_is_422(player_client, pending, handler_spy) -> None:
    r = await player_client.post(
        f"{URL}/{pending.id}/approve", headers=key(), json={"parent_note": "x" * 501}
    )
    assert r.status_code == 422
    ok = await player_client.post(
        f"{URL}/{pending.id}/approve", headers=key(2), json={"parent_note": "x" * 500}
    )
    assert ok.status_code == 200


async def test_failed_approve_does_not_burn_the_idempotency_key(
    player_client, pending, approval_registry
) -> None:
    from tests.fakes import FakeApprovalHandler

    approval_registry.register(FakeApprovalHandler(fail_on_approve=True))
    with pytest.raises(RuntimeError):
        await player_client.post(f"{URL}/{pending.id}/approve", headers=key())
    approval_registry.register(FakeApprovalHandler())
    ok = await player_client.post(f"{URL}/{pending.id}/approve", headers=key())
    assert ok.status_code == 200


async def test_child_and_other_roles_are_403(
    child_client, coach_client, trainer_client, admin_client, pending
) -> None:
    for c in (child_client, coach_client, trainer_client, admin_client):
        assert (await c.get(URL)).status_code == 403
        assert (await c.post(f"{URL}/{pending.id}/approve", headers=key())).status_code == 403
