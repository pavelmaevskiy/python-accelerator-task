from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.api.errors import register_exception_handlers
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
)


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    class Body(BaseModel):
        n: int

    @app.get("/e/{kind}")
    async def raise_it(kind: str) -> None:
        if kind == "500":
            raise RuntimeError("secret internals")
        raise {
            "401": AuthenticationError("INVALID_CREDENTIALS", "bad"),
            "403": PermissionDeniedError("FORBIDDEN", "no"),
            "404": NotFoundError("NOT_FOUND", "gone"),
            "409": ConflictError("EMAIL_TAKEN", "dup", details={"action": "login_then_redeem"}),
            "429": RateLimitedError("RATE_LIMITED", "slow", retry_after=30),
        }[kind]

    @app.post("/v")
    async def validate(b: Body) -> None: ...

    return app


async def test_domain_errors_map_to_status_and_envelope() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        for kind, status in [("401", 401), ("403", 403), ("404", 404), ("409", 409), ("429", 429)]:
            r = await c.get(f"/e/{kind}")
            assert r.status_code == status
            assert set(r.json()["error"]) == {"code", "message", "details"}
        assert (await c.get("/e/429")).headers["Retry-After"] == "30"
        assert (await c.get("/e/409")).json()["error"]["details"] == {"action": "login_then_redeem"}


async def test_pydantic_validation_is_422_with_errors_list() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.post("/v", json={"n": "x"})
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["errors"][0]["loc"] == ["body", "n"]
    assert "input" not in body["details"]["errors"][0]


async def test_unknown_route_uses_envelope() -> None:
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


async def test_unhandled_exception_is_500_envelope_without_leak() -> None:
    transport = ASGITransport(app=_app(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/e/500")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in r.text
