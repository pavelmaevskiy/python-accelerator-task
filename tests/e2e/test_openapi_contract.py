"""The generated OpenAPI document must match specs/api-designer-spec.md exactly."""

from typing import Any

import pytest

from app.main import create_app

EXPECTED = {
    ("post", "/api/v1/auth/login"), ("post", "/api/v1/auth/refresh"), ("post", "/api/v1/auth/logout"),
    ("post", "/api/v1/auth/register"), ("post", "/api/v1/auth/verify-email"),
    ("post", "/api/v1/auth/verify-email/resend"), ("post", "/api/v1/auth/password-reset/request"),
    ("post", "/api/v1/auth/password-reset/confirm"), ("post", "/api/v1/auth/impersonation/exit"),
    ("get", "/api/v1/me"), ("patch", "/api/v1/me"), ("post", "/api/v1/me/email-change"),
    ("put", "/api/v1/me/avatar"),
    ("get", "/api/v1/me/availability"), ("put", "/api/v1/me/availability"),
    ("get", "/api/v1/me/contexts"),
    ("get", "/api/v1/me/players"), ("post", "/api/v1/me/players"),
    ("get", "/api/v1/me/players/{player_id}"), ("patch", "/api/v1/me/players/{player_id}"),
    ("delete", "/api/v1/me/players/{player_id}"),
    ("post", "/api/v1/me/players/{player_id}/trainers"),
    ("delete", "/api/v1/me/players/{player_id}/trainers/{trainer_id}"),
    ("put", "/api/v1/me/players/{player_id}/availability"),
    ("get", "/api/v1/me/approvals"), ("post", "/api/v1/me/approvals/{approval_id}/approve"),
    ("post", "/api/v1/me/approvals/{approval_id}/deny"),
    ("get", "/api/v1/trainer/share-links"), ("post", "/api/v1/trainer/share-links"),
    ("delete", "/api/v1/trainer/share-links/{link_id}"),
    ("get", "/api/v1/trainer/players/{player_id}/availability"),
    ("get", "/api/v1/trainer/branding"), ("patch", "/api/v1/trainer/branding"),
    ("put", "/api/v1/trainer/branding/logo"),
    ("get", "/api/v1/public/share-links/{code}"), ("get", "/api/v1/public/trainers/{slug}/branding"),
    ("get", "/api/v1/admin/users"), ("post", "/api/v1/admin/users/trainers"),
    ("get", "/api/v1/admin/users/{user_id}"), ("patch", "/api/v1/admin/users/{user_id}"),
    ("delete", "/api/v1/admin/users/{user_id}"),
    ("post", "/api/v1/admin/users/{user_id}/deactivate"),
    ("post", "/api/v1/admin/users/{user_id}/reactivate"),
    ("post", "/api/v1/admin/users/{user_id}/impersonate"),
}  # fmt: skip

PUBLIC = {
    ("get", "/api/v1/public/share-links/{code}"),
    ("get", "/api/v1/public/trainers/{slug}/branding"),
    ("post", "/api/v1/auth/login"), ("post", "/api/v1/auth/refresh"),
    ("post", "/api/v1/auth/verify-email"), ("post", "/api/v1/auth/verify-email/resend"),
    ("post", "/api/v1/auth/password-reset/request"), ("post", "/api/v1/auth/password-reset/confirm"),
}  # fmt: skip
FORBIDDEN_RESPONSE_FIELDS = {
    "password_hash",
    "token_hash",
    "ip_hash",
    "user_agent_hash",
    "logo_key",
    "avatar_key",
}


@pytest.fixture(scope="module")
def openapi() -> dict[str, Any]:
    return create_app().openapi()


def operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(m, p): op for p, ops in spec["paths"].items() for m, op in ops.items()}


def resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    ref = node.get("$ref")
    return spec["components"]["schemas"][ref.rsplit("/", 1)[-1]] if ref else node


def refs_in(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            found.add(node["$ref"].rsplit("/", 1)[-1])
        for v in node.values():
            found |= refs_in(v)
    elif isinstance(node, list):
        for v in node:
            found |= refs_in(v)
    return found


def closure(spec: dict[str, Any], names: set[str]) -> set[str]:
    seen: set[str] = set()
    todo = list(names)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += list(refs_in(spec["components"]["schemas"][name]))
    return seen


def test_every_spec_endpoint_is_in_openapi_and_nothing_extra(openapi) -> None:
    assert set(operations(openapi)) == EXPECTED
    assert len(EXPECTED) == 44


def test_metadata_security_and_tags(openapi) -> None:
    assert (
        openapi["info"]["title"] == "Trainer Platform API" and openapi["info"]["version"] == "1.0.0"
    )
    scheme = openapi["components"]["securitySchemes"]["BearerAuth"]
    assert (
        scheme["type"] == "http"
        and scheme["scheme"] == "bearer"
        and scheme["bearerFormat"] == "JWT"
    )
    tags = {t["name"]: t for t in openapi["tags"]}
    assert set(tags) == {"auth", "me", "trainer", "public", "admin"}
    assert all(t["description"] for t in tags.values())


def test_health_check_is_not_part_of_the_contract(openapi) -> None:
    assert "/health" not in openapi["paths"]


def test_public_routes_override_security_empty(openapi) -> None:
    ops = operations(openapi)
    for key in PUBLIC:
        assert ops[key]["security"] == [], key
    for key, op in ops.items():
        if key not in PUBLIC and key != ("post", "/api/v1/auth/register"):
            assert op["security"] == [{"BearerAuth": []}], key
    assert ops[("post", "/api/v1/auth/register")]["security"] == [{}, {"BearerAuth": []}]


def test_every_operation_has_summary_tag_and_error_responses_use_ErrorResponse(openapi) -> None:
    for key, op in operations(openapi).items():
        assert op.get("summary"), key
        assert len(op["tags"]) == 1, key
        for status, response in op["responses"].items():
            if status.startswith(("4", "5")):
                schema = response["content"]["application/json"]["schema"]
                assert schema == {"$ref": "#/components/schemas/ErrorResponse"}, (key, status)
    assert "HTTPValidationError" not in openapi["components"]["schemas"]
    assert "ValidationError" not in openapi["components"]["schemas"]


def test_tag_matches_the_url_area(openapi) -> None:
    for (_, path), op in operations(openapi).items():
        assert path.split("/")[3] == op["tags"][0], path


def test_operation_ids_are_tag_prefixed_and_unique(openapi) -> None:
    ids = [op["operationId"] for op in operations(openapi).values()]
    assert len(ids) == len(set(ids))
    for op in operations(openapi).values():
        assert op["operationId"].startswith(f"{op['tags'][0]}_")
    assert "auth_login" in ids and "admin_erase_user" in ids and "me_approve" in ids


def test_no_response_schema_contains_forbidden_fields(openapi) -> None:
    response_roots: set[str] = set()
    for op in operations(openapi).values():
        for response in op["responses"].values():
            response_roots |= refs_in(response)
    for name in closure(openapi, response_roots):
        props = set(openapi["components"]["schemas"][name].get("properties", {}))
        assert not (props & FORBIDDEN_RESPONSE_FIELDS), (name, props & FORBIDDEN_RESPONSE_FIELDS)


def test_impersonation_response_has_no_refresh_token(openapi) -> None:
    props = openapi["components"]["schemas"]["ImpersonateResponse"]["properties"]
    assert "refresh_token" not in props
    assert {"access_token", "expires_at", "expires_in", "target"} <= set(props)


def test_idempotency_key_and_x_trainer_context_headers_documented(openapi) -> None:
    ops = operations(openapi)
    for key in (
        ("post", "/api/v1/me/approvals/{approval_id}/approve"),
        ("post", "/api/v1/me/approvals/{approval_id}/deny"),
    ):
        header = next(p for p in ops[key]["parameters"] if p["name"] == "Idempotency-Key")
        assert header["in"] == "header" and header["required"] is True
        assert header["schema"]["minLength"] == 8 and header["schema"]["maxLength"] == 128
    for key in (
        ("get", "/api/v1/me"),
        ("get", "/api/v1/me/contexts"),
        ("get", "/api/v1/me/players"),
    ):
        names = {p["name"] for p in ops[key].get("parameters", [])}
        assert "X-Trainer-Context" in names, key


def test_all_request_schemas_forbid_additional_properties(openapi) -> None:
    checked = 0
    for key, op in operations(openapi).items():
        body = op.get("requestBody")
        if not body:
            continue
        schema = body["content"]["application/json"]["schema"]
        candidates = schema.get("anyOf", [schema])
        for candidate in candidates:
            if "$ref" in candidate:
                resolved = resolve(openapi, candidate)
                assert resolved.get("additionalProperties") is False, (key, candidate)
                checked += 1
    assert checked >= 25


def test_request_models_have_examples_on_key_endpoints(openapi) -> None:
    schemas = openapi["components"]["schemas"]
    for name in (
        "LoginRequest",
        "RegisterRequest",
        "CreateShareLink",
        "ProvisionTrainer",
        "CreatePlayer",
    ):
        assert schemas[name].get("examples"), name


def test_paginated_endpoints_document_the_cursor_envelope(openapi) -> None:
    ops = operations(openapi)
    for key in (
        ("get", "/api/v1/trainer/share-links"),
        ("get", "/api/v1/me/players"),
        ("get", "/api/v1/me/approvals"),
        ("get", "/api/v1/admin/users"),
    ):
        schema = resolve(
            openapi, ops[key]["responses"]["200"]["content"]["application/json"]["schema"]
        )
        assert set(schema["properties"]) == {"items", "next_cursor", "has_more"}, key
        params = {p["name"] for p in ops[key]["parameters"]}
        assert {"limit", "cursor"} <= params, key


def test_status_codes_match_the_spec_for_created_and_empty_responses(openapi) -> None:
    ops = operations(openapi)
    assert "201" in ops[("post", "/api/v1/auth/register")]["responses"]
    assert "200" in ops[("post", "/api/v1/auth/register")]["responses"]
    assert "201" in ops[("post", "/api/v1/me/players")]["responses"]
    assert "200" in ops[("post", "/api/v1/me/players/{player_id}/trainers")]["responses"]
    for key in (
        ("post", "/api/v1/auth/logout"), ("post", "/api/v1/auth/verify-email"),
        ("post", "/api/v1/auth/password-reset/confirm"), ("post", "/api/v1/auth/impersonation/exit"),
        ("delete", "/api/v1/me/players/{player_id}"), ("delete", "/api/v1/admin/users/{user_id}"),
        ("delete", "/api/v1/trainer/share-links/{link_id}"),
    ):  # fmt: skip
        assert "204" in ops[key]["responses"], key
    for key in (
        ("post", "/api/v1/auth/verify-email/resend"),
        ("post", "/api/v1/auth/password-reset/request"),
    ):
        assert "202" in ops[key]["responses"], key


def test_schema_generation_works_outside_the_test_container() -> None:
    import json

    assert len(json.dumps(create_app().openapi())) > 10_000
