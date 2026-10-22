import asyncio

import pytest

pytestmark = pytest.mark.integration

URL = "/api/v1/me/approvals"


async def test_10_parallel_approves_with_same_key_run_handler_once_and_all_return_200(
    player_client, pending, handler_spy
) -> None:
    headers = {"Idempotency-Key": "same-key-0001"}
    results = await asyncio.gather(
        *[player_client.post(f"{URL}/{pending.id}/approve", headers=headers) for _ in range(10)]
    )
    assert [r.status_code for r in results] == [200] * 10
    assert len({r.text for r in results}) == 1  # identical stored response
    assert len(handler_spy.approved) == 1


async def test_10_parallel_approves_with_different_keys_one_200_rest_409(
    player_client, pending, handler_spy
) -> None:
    results = await asyncio.gather(
        *[
            player_client.post(
                f"{URL}/{pending.id}/approve", headers={"Idempotency-Key": f"key-number-{i:03d}"}
            )
            for i in range(10)
        ]
    )
    assert sorted(r.status_code for r in results) == [200] + [409] * 9
    assert {r.json()["error"]["code"] for r in results if r.status_code == 409} == {
        "APPROVAL_NOT_PENDING"
    }
    assert len(handler_spy.approved) == 1


async def test_parallel_approve_and_deny_with_different_keys_yield_exactly_one_decision(
    player_client, pending, handler_spy
) -> None:
    approve = player_client.post(
        f"{URL}/{pending.id}/approve", headers={"Idempotency-Key": "key-approve-1"}
    )
    deny = player_client.post(
        f"{URL}/{pending.id}/deny", headers={"Idempotency-Key": "key-deny-0001"}
    )
    results = await asyncio.gather(approve, deny)
    assert sorted(r.status_code for r in results) == [200, 409]
