import asyncio

import pytest
from sqlalchemy import func, select

from app.models.identity import ShareLink, TrainerPlayerLink, User

pytestmark = pytest.mark.integration

REGISTER = "/api/v1/auth/register"
PW = "tr1cky-Horse-staple-9"


async def register_one(client, link, i: int, *, email: str | None = None):
    return await client.post(
        REGISTER,
        params={"share_code": link.code},
        json={
            "email": email or f"user{i}@x.co",
            "password": PW,
            "first_name": "A",
            "last_name": "B",
        },
    )


async def test_20_parallel_redemptions_of_one_coach_unique_link_yield_exactly_one_success(
    client, coach_link, db_session
) -> None:
    results = await asyncio.gather(
        *[register_one(client, coach_link, i, email="coach@x.co") for i in range(20)]
    )
    assert sorted(r.status_code for r in results) == [201] + [404] * 19
    assert all(
        r.json()["error"]["code"] == "SHARE_LINK_INVALID" for r in results if r.status_code == 404
    )
    link = await db_session.get(ShareLink, coach_link.id, populate_existing=True)
    assert link.use_count == 1


async def test_200_parallel_static_redemptions_lose_no_use_count_writes(
    client, static_link, db_session
) -> None:
    results = await asyncio.gather(*[register_one(client, static_link, i) for i in range(200)])
    assert {r.status_code for r in results} == {201}
    link = await db_session.get(ShareLink, static_link.id, populate_existing=True)
    assert link.use_count == 200
    n_links = (
        await db_session.execute(select(func.count()).select_from(TrainerPlayerLink))
    ).scalar_one()
    assert n_links == 200


async def test_same_email_race_yields_one_201_and_rest_409_EMAIL_TAKEN(
    client, static_link, db_session
) -> None:
    results = await asyncio.gather(
        *[register_one(client, static_link, i, email="same@x.co") for i in range(10)]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201] + [409] * 9
    assert {r.json()["error"]["code"] for r in results if r.status_code == 409} <= {
        "EMAIL_TAKEN",
        "ACCOUNT_EXISTS",
    }
    n = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.email == "same@x.co")
        )
    ).scalar_one()
    assert n == 1
    link = await db_session.get(ShareLink, static_link.id, populate_existing=True)
    assert link.use_count == 1  # losers rolled back their increment
