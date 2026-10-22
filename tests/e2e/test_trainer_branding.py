import pytest

from app.models.identity import TrainerProfile

pytestmark = pytest.mark.integration

URL = "/api/v1/trainer/branding"
LOGO = f"{URL}/logo"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><rect width="1" height="1"/></svg>'


async def _presign(c, content_type="image/png", length=100) -> str:
    r = await c.put(LOGO, json={"content_type": content_type, "content_length": length})
    assert r.status_code == 200, r.text
    return r.json()["key"]


async def test_get_branding_returns_own_branding(trainer_client) -> None:
    r = await trainer_client.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body["business_name"] == "Acme Tennis" and body["slug"].startswith("acme-")
    assert body["logo_url"] is None and body["brand_primary_color"] is None


async def test_patch_updates_fields_and_invalid_hex_color_is_422(trainer_client) -> None:
    r = await trainer_client.patch(
        URL,
        json={
            "business_name": "Net Gains",
            "address": "1 Court St",
            "website": "https://netgains.example/",
            "description": "Best clinics",
            "brand_primary_color": "#FF8800",
            "brand_secondary_color": "#00aaFF",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["business_name"] == "Net Gains" and body["brand_primary_color"] == "#FF8800"
    assert body["website"] == "https://netgains.example/" and body["address"] == "1 Court St"
    for bad in (
        {"brand_primary_color": "red"},
        {"brand_secondary_color": "#12345"},
        {"brand_primary_color": "#GGGGGG"},
        {"website": "not a url"},
        {"slug": "new"},
    ):
        assert (await trainer_client.patch(URL, json=bad)).status_code == 422


async def test_patch_is_partial_and_null_clears(trainer_client) -> None:
    await trainer_client.patch(URL, json={"address": "A", "brand_primary_color": "#112233"})
    r = await trainer_client.patch(URL, json={"address": None})
    assert r.json()["address"] is None and r.json()["brand_primary_color"] == "#112233"
    assert r.json()["business_name"] == "Acme Tennis"


async def test_logo_presign_allows_png_svg_webp_up_to_2mib_only(
    trainer_client, fake_storage
) -> None:
    for ct in ("image/png", "image/svg+xml", "image/webp"):
        key = await _presign(trainer_client, ct, 2 * 1024 * 1024)
        assert key.startswith(f"quarantine/{trainer_client.user.id}/")
    for payload in (
        {"content_type": "image/jpeg", "content_length": 10},
        {"content_type": "image/png", "content_length": 2 * 1024 * 1024 + 1},
        {"content_type": "image/png", "content_length": 0},
    ):
        assert (await trainer_client.put(LOGO, json=payload)).status_code == 422


async def test_logo_key_confirm_publishes_png_and_cleans_quarantine(
    trainer_client, fake_storage, db_session
) -> None:
    key = await _presign(trainer_client)
    fake_storage.seed(key, PNG, "image/png")
    r = await trainer_client.patch(URL, json={"logo_key": key})
    assert r.status_code == 200
    assert r.json()["logo_url"].startswith("https://cdn.test/logos/") and "quarantine" not in r.text
    assert fake_storage.keys("quarantine/") == [] and len(fake_storage.keys("logos/")) == 1
    assert "logo_key" not in r.text


async def test_logo_key_confirm_sanitises_svg_and_rejects_oversize_or_bad_magic(
    trainer_client, fake_storage
) -> None:
    key = await _presign(trainer_client, "image/svg+xml")
    fake_storage.seed(key, SVG, "image/svg+xml")
    assert (await trainer_client.patch(URL, json={"logo_key": key})).status_code == 200
    [final] = fake_storage.keys("logos/")
    stored = fake_storage.objects[final].decode()
    assert "script" not in stored and "<rect" in stored and final.endswith(".svg")

    big = await _presign(trainer_client)
    fake_storage.seed(big, PNG + b"0" * (2 * 1024 * 1024), "image/png")
    r = await trainer_client.patch(URL, json={"logo_key": big})
    assert r.status_code == 422 and r.json()["error"]["code"] == "UPLOAD_INVALID"

    jpeg_as_logo = await _presign(trainer_client)
    fake_storage.seed(jpeg_as_logo, b"\xff\xd8\xff" + b"0" * 50, "image/png")
    assert (await trainer_client.patch(URL, json={"logo_key": jpeg_as_logo})).status_code == 422

    broken_svg = await _presign(trainer_client, "image/svg+xml")
    fake_storage.seed(broken_svg, b"<svg xmlns='http://www.w3.org/2000/svg'><", "image/svg+xml")
    assert (await trainer_client.patch(URL, json={"logo_key": broken_svg})).status_code == 422
    assert fake_storage.keys("quarantine/") == []  # rejects are cleaned up


async def test_logo_key_not_owned_is_404_UPLOAD_NOT_FOUND(
    trainer_client, make_trainer, client_for, fake_storage
) -> None:
    other = client_for(await make_trainer())
    key = await _presign(other)
    fake_storage.seed(key, PNG, "image/png")
    r = await trainer_client.patch(URL, json={"logo_key": key})
    assert r.status_code == 404 and r.json()["error"]["code"] == "UPLOAD_NOT_FOUND"
    missing = await trainer_client.patch(
        URL, json={"logo_key": f"quarantine/{trainer_client.user.id}/never-uploaded"}
    )
    assert missing.status_code == 404


async def test_replacing_logo_enqueues_deletion_of_the_old_one(
    trainer_client, fake_storage, fake_queue
) -> None:
    for _ in range(2):
        key = await _presign(trainer_client)
        fake_storage.seed(key, PNG, "image/png")
        await trainer_client.patch(URL, json={"logo_key": key})
    assert len(fake_queue.named("delete_s3_object")) == 1


async def test_patch_deletes_public_branding_cache_key(
    trainer_client, redis, client, db_session
) -> None:
    from sqlalchemy import select

    slug = (await db_session.execute(select(TrainerProfile.slug))).scalar_one()
    await client.get(f"/api/v1/public/trainers/{slug}/branding")  # fills the cache
    assert await redis.exists(f"public:branding:{slug}") == 1
    await trainer_client.patch(URL, json={"business_name": "Renamed"})
    assert await redis.exists(f"public:branding:{slug}") == 0
    fresh = await client.get(f"/api/v1/public/trainers/{slug}/branding")
    assert fresh.json()["business_name"] == "Renamed"


async def test_failed_patch_keeps_cache(trainer_client, redis, client, db_session) -> None:
    from sqlalchemy import select

    slug = (await db_session.execute(select(TrainerProfile.slug))).scalar_one()
    await client.get(f"/api/v1/public/trainers/{slug}/branding")
    await trainer_client.patch(URL, json={"logo_key": f"quarantine/{trainer_client.user.id}/x"})
    assert await redis.exists(f"public:branding:{slug}") == 1


async def test_coach_and_player_get_403_on_trainer_branding(
    coach_client, player_client, admin_client
) -> None:
    for c in (coach_client, player_client, admin_client):
        assert (await c.get(URL)).status_code == 403
        assert (await c.patch(URL, json={})).status_code == 403
        assert (
            await c.put(LOGO, json={"content_type": "image/png", "content_length": 1})
        ).status_code == 403
