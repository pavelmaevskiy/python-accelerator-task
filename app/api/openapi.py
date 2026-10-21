"""OpenAPI metadata and post-processing so the docs match the real error contract."""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute

API_TITLE = "Trainer Platform API"
API_VERSION = "1.0.0"
API_DESCRIPTION = (
    "Epic-01: identity, tenancy and authentication. Every error uses the single envelope "
    "`{error: {code, message, details}}`. Lists are cursor-paged: `{items, next_cursor, has_more}`."
)
TAGS_METADATA: list[dict[str, str]] = [
    {"name": "auth", "description": "Login, token rotation, registration, verification, reset."},
    {
        "name": "me",
        "description": "The current user: profile, availability, dependents, approvals.",
    },
    {
        "name": "trainer",
        "description": "Trainer tenant: share links, player availability, branding.",
    },
    {"name": "public", "description": "Unauthenticated: share-link preview and trainer branding."},
    {
        "name": "admin",
        "description": "Super-admin: user directory, provisioning, impersonation, erasure.",
    },
]
# Operations that are public or optionally authenticated (security is declared explicitly).
OPTIONAL_AUTH_OPERATIONS = {"auth_register"}
_ERROR_REF = "#/components/schemas/ErrorResponse"
_FASTAPI_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")


def unique_operation_id(route: APIRoute) -> str:
    """Stable ids for client SDKs: ``{tag}_{route.name}``."""
    tag = route.tags[0] if route.tags else "default"
    return f"{tag}_{route.name}"


def _replace_validation_refs(node: Any) -> None:
    """Point FastAPI's default 422 body at our envelope instead of HTTPValidationError."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.rsplit("/", 1)[-1] in _FASTAPI_VALIDATION_SCHEMAS:
            node["$ref"] = _ERROR_REF
        for value in node.values():
            _replace_validation_refs(value)
    elif isinstance(node, list):
        for value in node:
            _replace_validation_refs(value)


def build_openapi(app: FastAPI) -> dict[str, Any]:
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        routes=app.routes,
        tags=TAGS_METADATA,
    )
    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
        "description": "Access token from POST /auth/login or /auth/refresh.",
    }
    _replace_validation_refs(schema["paths"])
    for name in _FASTAPI_VALIDATION_SCHEMAS:
        components.get("schemas", {}).pop(name, None)
    for operations in schema["paths"].values():
        for operation in operations.values():
            if operation["operationId"] in OPTIONAL_AUTH_OPERATIONS:
                operation["security"] = [{}, {"BearerAuth": []}]
            elif "security" not in operation:
                operation["security"] = []  # public: explicitly no authentication
    app.openapi_schema = schema
    return schema
