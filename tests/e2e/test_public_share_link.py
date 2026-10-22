import pytest
from sqlalchemy import select, update

from app.models.identity import ShareLink, ShareLinkEvent

pytestmark = pytest.mark.integration


@pytest.fixture
async def link(trainer_client):
    r = await trainer_client.post("/api/v1/trainer/share-links", json={"kind": "player_static"})
    return r.json()


def url(code: str) -> str:
    return f"/api/v1/public/share-links/{code}"


async def test_preview_valid_link_exposes_only_branding_fields_and_records_opened_event(
    client, link, trainer_client, db_session
) -> None:
    r = await client.get(url(link["code"]), headers={"User-Agent": "pytest"})
    assert r.status_code == 200
    assert r.json() == {
        "trainer_business_name": "Acme Tennis",
        "trainer_slug": r.json()["trainer_slug"],
        "logo_url": None,
        "kind": "player_static",
        "is_valid": True,
    }
    events = (await db_session.execute(select(ShareLinkEvent))).scalars().all()
    assert len(events) == 1 and events[0].kind.value == "opened"
    assert len(events[0].ip_hash) == 64 and events[0].ip_hash != "127.0.0.1"
    assert events[0].user_agent_hash != "pytest"


async def test_preview_never_exposes_target_email(client, trainer_client) -> None:
    created = (
        await trainer_client.post(
            "/api/v1/trainer/share-links", json={"kind": "coach_unique", "target_email": "c@x.co"}
        )
    ).json()
    r = await client.get(url(created["code"]))
    assert r.status_code == 200 and "c@x.co" not in r.text and "target_email" not in r.json()


@pytest.mark.parametrize(
    "state", ["unknown", "inactive", "expired", "exhausted", "trainer_inactive"]
)
async def test_preview_invalid_states_are_one_identical_404_SHARE_LINK_INVALID(
    client, trainer_client, db_session, clock, state
) -> None:
    created = (
        await trainer_client.post(
            "/api/v1/trainer/share-links", json={"kind": "coach_unique", "target_email": "c@x.co"}
        )
    ).json()
    code = created["code"]
    if state == "unknown":
        code = "x" * 22
    elif state == "inactive":
        await db_session.execute(update(ShareLink).values(is_active=False))
    elif state == "expired":
        clock.advance(days=7, seconds=1)
    elif state == "exhausted":
        await db_session.execute(update(ShareLink).values(use_count=1))
    elif state == "trainer_inactive":
        from app.models.identity import User

        await db_session.execute(
            update(User).where(User.id == trainer_client.user.id).values(status="inactive")
        )
    await db_session.commit()
    r = await client.get(url(code))
    assert r.status_code == 404
    assert r.json() == {
        "error": {
            "code": "SHARE_LINK_INVALID",
            "message": "This link is not valid",
            "details": None,
        }
    }
    assert (await db_session.execute(select(ShareLinkEvent))).first() is None


async def test_preview_61st_request_per_minute_is_429(client, link) -> None:
    for _ in range(60):
        assert (await client.get(url(link["code"]))).status_code == 200
    r = await client.get(url(link["code"]))
    assert r.status_code == 429 and "Retry-After" in r.headers


async def test_preview_requires_no_authentication(client, link) -> None:
    assert (await client.get(url(link["code"]))).status_code == 200
