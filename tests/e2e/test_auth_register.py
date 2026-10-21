import uuid

import pytest
from sqlalchemy import select

from app.models.identity import (
    CoachMembership,
    CoachProfile,
    PlayerProfile,
    Profile,
    ShareLink,
    ShareLinkEvent,
    TrainerPlayerLink,
    User,
)

pytestmark = pytest.mark.integration

REGISTER = "/api/v1/auth/register"
PW = "tr1cky-Horse-staple-9"


def body(**kw: object) -> dict[str, object]:
    base = {"email": "new@x.co", "password": PW, "first_name": "A", "last_name": "B"}
    return {**base, **kw}


async def test_register_via_player_static_link_creates_user_profile_player_and_active_link(
    client, static_link, db_session, fake_queue
) -> None:
    r = await client.post(
        REGISTER,
        params={"share_code": static_link.code},
        json=body(birth_date="1990-01-01", phone="+15551234567", timezone="Europe/Warsaw"),
    )
    assert r.status_code == 201
    b = r.json()
    assert b["tokens"] is None and b["verification_required"] is True
    assert b["linked_trainer_id"] == str(static_link.trainer_id)
    assert b["user"]["role"] == "player" and b["user"]["is_email_verified"] is False

    user = (await db_session.execute(select(User).where(User.email == "new@x.co"))).scalar_one()
    profile = await db_session.get(Profile, user.id)
    assert (profile.phone, profile.timezone) == ("+15551234567", "Europe/Warsaw")
    player = (await db_session.execute(select(PlayerProfile))).scalar_one()
    assert player.owner_user_id == user.id and player.is_dependent is False
    assert player.birth_date.isoformat() == "1990-01-01"
    link = (await db_session.execute(select(TrainerPlayerLink))).scalar_one()
    assert link.source.value == "share_link" and link.status.value == "active"
    assert link.trainer_id == static_link.trainer_id and link.share_link_id == static_link.id
    assert (await db_session.get(ShareLink, static_link.id, populate_existing=True)).use_count == 1
    assert {j.name for j in fake_queue.jobs} == {"send_welcome_email", "send_verification_email"}


async def test_register_without_birth_date_still_creates_own_profile_and_link(
    client, static_link, db_count
) -> None:
    r = await client.post(REGISTER, params={"share_code": static_link.code}, json=body())
    assert r.status_code == 201
    assert await db_count(PlayerProfile) == 1 and await db_count(TrainerPlayerLink) == 1


async def test_register_records_registered_event(client, static_link, db_session) -> None:
    await client.post(REGISTER, params={"share_code": static_link.code}, json=body())
    event = (await db_session.execute(select(ShareLinkEvent))).scalar_one()
    assert event.kind.value == "registered" and event.share_link_id == static_link.id


async def test_register_via_coach_link_with_matching_email_issues_tokens_and_is_verified(
    client, coach_link, db_session, fake_queue
) -> None:
    r = await client.post(
        REGISTER,
        params={"share_code": coach_link.code},
        json=body(email="Coach@X.co", birth_date="1990-01-01"),
    )
    assert r.status_code == 201
    b = r.json()
    assert b["tokens"]["access_token"] and b["tokens"]["refresh_token"]
    assert b["verification_required"] is False and b["user"]["role"] == "coach"
    assert b["user"]["is_email_verified"] is True
    assert await db_session.get(CoachProfile, uuid.UUID(b["user"]["id"])) is not None
    m = (await db_session.execute(select(CoachMembership))).scalar_one()
    assert m.is_active and m.trainer_id == coach_link.trainer_id
    assert (await db_session.execute(select(PlayerProfile))).first() is None  # birth_date ignored
    link = await db_session.get(ShareLink, coach_link.id, populate_existing=True)
    assert link.use_count == 1
    assert [j.name for j in fake_queue.jobs] == ["send_welcome_email"]


async def test_coach_link_email_mismatch_is_403_LINK_EMAIL_MISMATCH(
    client, coach_link, db_session
) -> None:
    r = await client.post(
        REGISTER, params={"share_code": coach_link.code}, json=body(email="intruder@x.co")
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "LINK_EMAIL_MISMATCH"
    assert (await db_session.get(ShareLink, coach_link.id, populate_existing=True)).use_count == 0
    assert (await db_session.execute(select(User))).scalars().all() == [
        u
        for u in (await db_session.execute(select(User))).scalars().all()
        if u.role.value == "trainer"
    ]


async def test_exactly_one_of_share_code_or_camp_submission_id_else_422(
    client, static_link
) -> None:
    neither = await client.post(REGISTER, json=body())
    both = await client.post(
        REGISTER,
        params={"share_code": static_link.code, "camp_submission_id": str(uuid.uuid4())},
        json=body(),
    )
    for r in (neither, both):
        assert r.status_code == 422 and r.json()["error"]["code"] == "EXACTLY_ONE_SOURCE"


async def test_camp_submission_id_is_409_CAMP_SUBMISSION_UNAVAILABLE_until_epic08(client) -> None:
    r = await client.post(REGISTER, params={"camp_submission_id": str(uuid.uuid4())}, json=body())
    assert r.status_code == 409 and r.json()["error"]["code"] == "CAMP_SUBMISSION_UNAVAILABLE"


@pytest.mark.parametrize("state", ["unknown", "inactive", "expired", "exhausted"])
async def test_unknown_inactive_expired_exhausted_link_is_404_SHARE_LINK_INVALID(
    client, make_share_link, db_session, clock, state
) -> None:
    link = await make_share_link(kind="coach_unique", target_email="new@x.co")
    code = link.code
    if state == "unknown":
        code = "z" * 22
    elif state == "inactive":
        link.is_active = False
    elif state == "expired":
        clock.advance(days=8)
    elif state == "exhausted":
        link.use_count = 1
    await db_session.commit()
    r = await client.post(REGISTER, params={"share_code": code}, json=body())
    assert r.status_code == 404 and r.json()["error"]["code"] == "SHARE_LINK_INVALID"
    assert (await db_session.execute(select(User).where(User.email == "new@x.co"))).first() is None


async def test_existing_email_unauthenticated_is_409_ACCOUNT_EXISTS_with_details_action(
    client, static_link, make_user
) -> None:
    await make_user(email="new@x.co")
    r = await client.post(REGISTER, params={"share_code": static_link.code}, json=body())
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "ACCOUNT_EXISTS" and err["details"] == {"action": "login_then_redeem"}


async def test_authenticated_existing_account_redeems_link_200_and_only_adds_missing_link(
    player_client, make_share_link, db_session
) -> None:
    first = await make_share_link()
    second = await make_share_link()
    mine = player_client.profile
    for link in (first, second, first):  # third call: link already exists, nothing duplicated
        r = await player_client.post(REGISTER, params={"share_code": link.code}, json=body())
        assert r.status_code == 200
        assert r.json()["tokens"] is None
        assert r.json()["linked_trainer_id"] == str(link.trainer_id)
    links = (await db_session.execute(select(TrainerPlayerLink))).scalars().all()
    assert len(links) == 2 and all(lk.player_profile_id == mine.id for lk in links)
    assert (await db_session.execute(select(User).where(User.email == "new@x.co"))).first() is None


async def test_authenticated_redeem_reactivates_an_inactive_link(
    player_client, make_share_link, link_player, db_session
) -> None:
    link = await make_share_link()
    trainer = await db_session.get(User, link.trainer_id)
    await link_player(trainer, player_client.profile, status="inactive")
    r = await player_client.post(REGISTER, params={"share_code": link.code}, json=body())
    assert r.status_code == 200
    row = (await db_session.execute(select(TrainerPlayerLink))).scalar_one()
    assert row.status.value == "active"


async def test_authenticated_parent_links_only_listed_dependents(
    player_client, make_share_link, db_session
) -> None:
    kid = PlayerProfile(
        owner_user_id=player_client.user.id,
        display_name="Kid",
        birth_date=__import__("datetime").date(2015, 5, 5),
        gender="male",
        is_dependent=True,
    )
    db_session.add(kid)
    await db_session.commit()
    link = await make_share_link()
    r = await player_client.post(
        REGISTER,
        params={"share_code": link.code},
        json=body(player_profile_ids=[str(kid.id)]),
    )
    assert r.status_code == 200
    rows = (await db_session.execute(select(TrainerPlayerLink))).scalars().all()
    assert [row.player_profile_id for row in rows] == [kid.id]


async def test_empty_player_profile_ids_is_422(player_client, static_link) -> None:
    r = await player_client.post(
        REGISTER, params={"share_code": static_link.code}, json=body(player_profile_ids=[])
    )
    assert r.status_code == 422


async def test_player_profile_ids_not_owned_is_404(
    player_client, make_player, static_link, db_count
) -> None:
    stranger = await make_player()
    r = await player_client.post(
        REGISTER,
        params={"share_code": static_link.code},
        json=body(player_profile_ids=[str(stranger.id)]),
    )
    assert r.status_code == 404
    assert await db_count(TrainerPlayerLink) == 0


async def test_child_login_cannot_redeem_403_CHILD_CANNOT_REDEEM(child_client, static_link) -> None:
    r = await child_client.post(REGISTER, params={"share_code": static_link.code}, json=body())
    assert r.status_code == 403 and r.json()["error"]["code"] == "CHILD_CANNOT_REDEEM"


async def test_coach_link_redeemed_by_non_coach_is_409_WRONG_LINK_KIND(
    player_client, make_share_link
) -> None:
    link = await make_share_link(kind="coach_unique", target_email=player_client.user.email)
    r = await player_client.post(REGISTER, params={"share_code": link.code}, json=body())
    assert r.status_code == 409 and r.json()["error"]["code"] == "WRONG_LINK_KIND"


async def test_authenticated_coach_redeems_link_to_become_a_coach_of_new_trainer(
    make_user, client_for, make_share_link, db_session
) -> None:
    coach_user = await make_user(role="coach", email="free@x.co")
    db_session.add(CoachProfile(user_id=coach_user.id))
    await db_session.commit()
    link = await make_share_link(kind="coach_unique", target_email="free@x.co")
    r = await client_for(coach_user).post(REGISTER, params={"share_code": link.code}, json=body())
    assert r.status_code == 200
    m = (await db_session.execute(select(CoachMembership))).scalar_one()
    assert m.trainer_id == link.trainer_id and m.is_active


async def test_active_coach_elsewhere_gets_409_COACH_ALREADY_ASSIGNED(
    make_coach, client_for, make_share_link
) -> None:
    coach = await make_coach(email="busy@x.co")
    link = await make_share_link(kind="coach_unique", target_email="busy@x.co")
    r = await client_for(coach).post(REGISTER, params={"share_code": link.code}, json=body())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "COACH_ALREADY_ASSIGNED"
    assert "trainer_id" in r.json()["error"]["details"]


async def test_failure_midway_leaves_zero_rows(client, static_link, monkeypatch, db_count) -> None:
    from app.core.unit_of_work import UnitOfWork

    original = UnitOfWork.enqueue

    def broken(self, name, **kw):  # noqa: ANN001, ANN003
        if name == "send_welcome_email":
            raise RuntimeError("queue exploded")
        return original(self, name, **kw)

    monkeypatch.setattr(UnitOfWork, "enqueue", broken)
    import httpx

    with pytest.raises(RuntimeError):
        await client.post(REGISTER, params={"share_code": static_link.code}, json=body())
    # trainer owns one user row; the registrant must not exist
    assert await db_count(User) == 1
    assert await db_count(Profile) == 1
    assert await db_count(PlayerProfile) == 0 and await db_count(TrainerPlayerLink) == 0
    assert await db_count(ShareLinkEvent) == 0
    del httpx


async def test_weak_password_422_and_invalid_phone_422(client, static_link, db_count) -> None:
    weak = await client.post(
        REGISTER, params={"share_code": static_link.code}, json=body(password="passwordpassword")
    )
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "WEAK_PASSWORD"
    phone = await client.post(
        REGISTER, params={"share_code": static_link.code}, json=body(phone="12345")
    )
    assert phone.status_code == 422 and phone.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await db_count(PlayerProfile) == 0


@pytest.mark.parametrize(
    "extra",
    [{"timezone": "Mars/Base"}, {"birth_date": "1800-01-01"}, {"role": "super_admin"}],
)
async def test_invalid_fields_are_422(client, static_link, extra) -> None:
    r = await client.post(REGISTER, params={"share_code": static_link.code}, json=body(**extra))
    assert r.status_code == 422


async def test_share_code_must_be_22_chars(client) -> None:
    r = await client.post(REGISTER, params={"share_code": "short"}, json=body())
    assert r.status_code == 422


async def test_registered_unverified_player_cannot_log_in_until_verified(
    client, static_link, fake_queue
) -> None:
    await client.post(REGISTER, params={"share_code": static_link.code}, json=body())
    login = await client.post("/api/v1/auth/login", json={"email": "new@x.co", "password": PW})
    assert login.status_code == 403 and login.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    token = fake_queue.named("send_verification_email")[0].kwargs["token"]
    assert (
        await client.post("/api/v1/auth/verify-email", json={"token": token})
    ).status_code == 204
    login = await client.post("/api/v1/auth/login", json={"email": "new@x.co", "password": PW})
    assert login.status_code == 200
