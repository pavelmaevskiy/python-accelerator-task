import pytest
from sqlalchemy import select

from app.models.identity import RefreshToken, User

pytestmark = pytest.mark.integration

PW = "correct horse battery staple"


async def test_login_success_returns_token_pair_and_user(client, make_user, db_session) -> None:
    user = await make_user(email="ann@x.co", password=PW, verified=True, status="active")
    r = await client.post("/api/v1/auth/login", json={"email": "ann@x.co", "password": PW})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 900
    assert body["user"]["email"] == "ann@x.co" and "password_hash" not in body["user"]
    assert body["user"]["id"] == str(user.id)
    rows = (await db_session.execute(select(RefreshToken))).scalars().all()
    assert len(rows) == 1 and rows[0].token_hash != body["refresh_token"]
    refreshed = await db_session.get(User, user.id, populate_existing=True)
    assert refreshed.last_login_at is not None


async def test_login_email_is_case_insensitive(client, make_user) -> None:
    await make_user(email="ann@x.co", password=PW)
    r = await client.post("/api/v1/auth/login", json={"email": "ANN@x.co", "password": PW})
    assert r.status_code == 200


async def test_issued_access_token_authenticates_against_principal(client, make_user, app) -> None:
    from app.api.deps import PrincipalDep

    @app.get("/whoami")
    async def whoami(p: PrincipalDep) -> dict[str, str]:
        return {"id": str(p.user_id)}

    user = await make_user(email="ann@x.co", password=PW)
    tokens = (
        await client.post("/api/v1/auth/login", json={"email": "ann@x.co", "password": PW})
    ).json()
    r = await client.get("/whoami", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.json() == {"id": str(user.id)}


@pytest.mark.parametrize(
    ("email", "password"),
    [("nobody@x.co", "whatever-long-password"), ("ann@x.co", "wrong-password-123")],
)
async def test_unknown_user_and_bad_password_are_identical_401(
    client, make_user, email, password
) -> None:
    await make_user(email="ann@x.co", password=PW, verified=True, status="active")
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert r.json()["error"]["message"] == "Invalid email or password"


async def test_unverified_with_correct_password_is_403_without_tokens(client, make_user) -> None:
    await make_user(email="u@x.co", password=PW, verified=False, status="active")
    r = await client.post("/api/v1/auth/login", json={"email": "u@x.co", "password": PW})
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert "access_token" not in r.text


@pytest.mark.parametrize("status", ["inactive", "deleted", "pending"])
async def test_non_active_user_is_uniform_401(client, make_user, status) -> None:
    await make_user(email="s@x.co", password=PW, verified=True, status=status)
    r = await client.post("/api/v1/auth/login", json={"email": "s@x.co", "password": PW})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_sixth_attempt_for_same_email_is_429_with_retry_after(client) -> None:
    body = {"email": "spam@x.co", "password": "nope-nope-nope-1"}
    for _ in range(5):
        assert (await client.post("/api/v1/auth/login", json=body)).status_code == 401
    r = await client.post("/api/v1/auth/login", json=body)
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"
    assert 0 < int(r.headers["Retry-After"]) <= 60


async def test_eleventh_attempt_from_same_ip_is_429_even_for_different_emails(client) -> None:
    for i in range(10):
        r = await client.post(
            "/api/v1/auth/login", json={"email": f"u{i}@x.co", "password": "nope-nope-nope-1"}
        )
        assert r.status_code == 401
    r = await client.post(
        "/api/v1/auth/login", json={"email": "u99@x.co", "password": "nope-nope-nope-1"}
    )
    assert r.status_code == 429


async def test_extra_field_in_body_is_422(client) -> None:
    r = await client.post(
        "/api/v1/auth/login", json={"email": "a@x.co", "password": "x", "role": "super_admin"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_invalid_email_is_422(client) -> None:
    r = await client.post("/api/v1/auth/login", json={"email": "not-an-email", "password": "x"})
    assert r.status_code == 422
