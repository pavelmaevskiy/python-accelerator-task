import json

import pytest
from sqlalchemy import select, update

from app.models.identity import TrainerProfile, User

pytestmark = pytest.mark.integration


@pytest.fixture
async def branded(make_trainer, db_session):
    trainer = await make_trainer(slug="club-one", business_name="Club One")
    await db_session.execute(
        update(TrainerProfile)
        .where(TrainerProfile.user_id == trainer.id)
        .values(
            brand_primary_color="#112233",
            brand_secondary_color="#AABBCC",
            address="secret street 1",
            description="private-ish",
            logo_key="logos/x/y.png",
        )
    )
    await db_session.commit()
    return trainer


def url(slug: str = "club-one") -> str:
    return f"/api/v1/public/trainers/{slug}/branding"


async def test_public_branding_exposes_exactly_four_fields_with_cache_control_300(
    client, branded
) -> None:
    r = await client.get(url())
    assert r.status_code == 200
    assert r.json() == {
        "business_name": "Club One",
        "logo_url": "https://cdn.test/logos/x/y.png",
        "brand_primary_color": "#112233",
        "brand_secondary_color": "#AABBCC",
    }
    assert r.headers["Cache-Control"] == "public, max-age=300"
    for hidden in ("address", "secret", "slug", "description", "user_id"):
        assert hidden not in r.text


async def test_public_branding_is_read_through_redis_with_ttl_300(
    client, redis, branded, db_session
) -> None:
    assert await redis.exists("public:branding:club-one") == 0
    await client.get(url())
    ttl = await redis.ttl("public:branding:club-one")
    assert 0 < ttl <= 300
    cached = json.loads(await redis.get("public:branding:club-one"))
    assert set(cached) == {
        "business_name",
        "logo_url",
        "brand_primary_color",
        "brand_secondary_color",
    }
    # second read is served from Redis even if the row changed underneath
    await db_session.execute(update(TrainerProfile).values(business_name="Changed Behind Cache"))
    await db_session.commit()
    assert (await client.get(url())).json()["business_name"] == "Club One"


async def test_unknown_slug_is_404_and_not_cached(client, redis) -> None:
    r = await client.get(url("nope"))
    assert r.status_code == 404 and "Cache-Control" not in r.headers
    assert await redis.exists("public:branding:nope") == 0


async def test_inactive_trainer_branding_is_404(client, branded, db_session) -> None:
    await db_session.execute(update(User).where(User.id == branded.id).values(status="inactive"))
    await db_session.commit()
    assert (await client.get(url())).status_code == 404
    assert (await db_session.execute(select(TrainerProfile))).first() is not None
