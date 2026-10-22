"""Route-level permission matrix from specs/api-designer-spec.md ("Permission matrix").

Every cell is asserted: a disallowed role gets 403; an allowed role gets anything but 401/403
(the request body may be rejected with 422/404 afterwards, which is fine for this suite).
"""

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

ID = str(uuid4())
PLAYER, CHILD, COACH, TRAINER, ADMIN, IMPERSONATED = (
    "player", "child", "coach", "trainer", "super_admin", "impersonated",
)  # fmt: skip
ALL = {PLAYER, CHILD, COACH, TRAINER, ADMIN, IMPERSONATED}

# (method, path, roles that may call it). "impersonated" acts as a *player* target here.
MATRIX: list[tuple[str, str, set[str]]] = [
    # /me: profile, avatar for everyone; email-change is not available to children
    ("GET", "/api/v1/me", ALL),
    ("PATCH", "/api/v1/me", ALL),
    ("PUT", "/api/v1/me/avatar", ALL),
    ("POST", "/api/v1/me/email-change", ALL - {CHILD}),
    # /me/availability: player, child, coach (trainer and admin 403)
    ("GET", "/api/v1/me/availability", {PLAYER, CHILD, COACH, IMPERSONATED}),
    ("PUT", "/api/v1/me/availability", {PLAYER, CHILD, COACH, IMPERSONATED}),
    # /me/contexts: players incl. child
    ("GET", "/api/v1/me/contexts", {PLAYER, CHILD, IMPERSONATED}),
    # /me/players*, /me/approvals*: parents only (child 403)
    ("GET", "/api/v1/me/players", {PLAYER, IMPERSONATED}),
    ("POST", "/api/v1/me/players", {PLAYER, IMPERSONATED}),
    ("GET", f"/api/v1/me/players/{ID}", {PLAYER, IMPERSONATED}),
    ("PATCH", f"/api/v1/me/players/{ID}", {PLAYER, IMPERSONATED}),
    ("DELETE", f"/api/v1/me/players/{ID}", {PLAYER, IMPERSONATED}),
    ("POST", f"/api/v1/me/players/{ID}/trainers", {PLAYER, IMPERSONATED}),
    ("DELETE", f"/api/v1/me/players/{ID}/trainers/{ID}", {PLAYER, IMPERSONATED}),
    ("PUT", f"/api/v1/me/players/{ID}/availability", {PLAYER, IMPERSONATED}),
    ("GET", "/api/v1/me/approvals", {PLAYER, IMPERSONATED}),
    ("POST", f"/api/v1/me/approvals/{ID}/approve", {PLAYER, IMPERSONATED}),
    ("POST", f"/api/v1/me/approvals/{ID}/deny", {PLAYER, IMPERSONATED}),
    # /trainer/*: trainers only
    ("GET", "/api/v1/trainer/share-links", {TRAINER}),
    ("POST", "/api/v1/trainer/share-links", {TRAINER}),
    ("DELETE", f"/api/v1/trainer/share-links/{ID}", {TRAINER}),
    ("GET", f"/api/v1/trainer/players/{ID}/availability", {TRAINER}),
    ("GET", "/api/v1/trainer/branding", {TRAINER}),
    ("PATCH", "/api/v1/trainer/branding", {TRAINER}),
    ("PUT", "/api/v1/trainer/branding/logo", {TRAINER}),
    # /admin/*: super admins only, and never while impersonating
    ("GET", "/api/v1/admin/users", {ADMIN}),
    ("POST", "/api/v1/admin/users/trainers", {ADMIN}),
    ("GET", f"/api/v1/admin/users/{ID}", {ADMIN}),
    ("PATCH", f"/api/v1/admin/users/{ID}", {ADMIN}),
    ("DELETE", f"/api/v1/admin/users/{ID}", {ADMIN}),
    ("POST", f"/api/v1/admin/users/{ID}/deactivate", {ADMIN}),
    ("POST", f"/api/v1/admin/users/{ID}/reactivate", {ADMIN}),
    ("POST", f"/api/v1/admin/users/{ID}/impersonate", {ADMIN}),
    # auth routes that need a token
    ("POST", "/api/v1/auth/logout", ALL),
]


@pytest.fixture
async def matrix_clients(
    player_client, child_client, coach_client, trainer_client, admin_client, impersonated_client
):
    return {
        PLAYER: player_client,
        CHILD: child_client,
        COACH: coach_client,
        TRAINER: trainer_client,
        ADMIN: admin_client,
        IMPERSONATED: impersonated_client,
    }


@pytest.mark.parametrize("role", sorted(ALL))
async def test_matrix_row_for_role(role, matrix_clients) -> None:
    client = matrix_clients[role]
    mismatches: list[str] = []
    for method, path, allowed in MATRIX:
        r = await client.request(method, path, json={})
        if role in allowed:
            if r.status_code in (401, 403):
                mismatches.append(f"{role} {method} {path}: expected access, got {r.status_code}")
        elif r.status_code != 403:
            mismatches.append(f"{role} {method} {path}: expected 403, got {r.status_code}")
    assert not mismatches, "\n".join(mismatches)


async def test_forbidden_cells_use_the_documented_error_codes(matrix_clients) -> None:
    child = await matrix_clients[CHILD].get("/api/v1/me/players")
    assert child.json()["error"]["code"] == "CHILD_FORBIDDEN"
    coach = await matrix_clients[COACH].get("/api/v1/me/contexts")
    assert coach.json()["error"]["code"] == "FORBIDDEN"
    imp = await matrix_clients[IMPERSONATED].get("/api/v1/admin/users")
    assert imp.json()["error"]["code"] == "ADMIN_FORBIDDEN_WHILE_IMPERSONATING"
    email = await matrix_clients[CHILD].post("/api/v1/me/email-change", json={})
    assert email.json()["error"]["code"] == "CHILD_CANNOT_CHANGE_EMAIL"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        (m, p)
        for m, p, _ in MATRIX
        if p.startswith(("/api/v1/me", "/api/v1/trainer", "/api/v1/admin"))
    ]
    + [("POST", "/api/v1/auth/logout"), ("POST", "/api/v1/auth/impersonation/exit")],
)
async def test_every_protected_route_requires_authentication(client, method, path) -> None:
    r = await client.request(method, path, json={})
    assert r.status_code == 401, (method, path, r.status_code)
    assert r.json()["error"]["code"] in {"NOT_AUTHENTICATED", "INVALID_TOKEN"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/refresh"),
        ("POST", "/api/v1/auth/verify-email"),
        ("POST", "/api/v1/auth/verify-email/resend"),
        ("POST", "/api/v1/auth/password-reset/request"),
        ("POST", "/api/v1/auth/password-reset/confirm"),
        ("POST", "/api/v1/auth/register"),
        ("GET", "/api/v1/public/share-links/" + "a" * 22),
        ("GET", "/api/v1/public/trainers/nobody/branding"),
    ],
)
async def test_public_routes_never_demand_a_token(client, method, path) -> None:
    r = await client.request(method, path, json={})
    assert r.status_code not in (401, 403) or r.json()["error"]["code"] in {
        "INVALID_CREDENTIALS",
        "REFRESH_INVALID",
    }, (method, path, r.status_code)
