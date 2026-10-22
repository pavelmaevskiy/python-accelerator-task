"""Reusable OpenAPI ``responses`` declarations for the single error envelope."""

from typing import Any

from app.api.schemas import ErrorResponse

_DESCRIPTIONS = {
    400: "Bad request",
    401: "Authentication failed or required",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    422: "Validation error",
    429: "Rate limited",
}


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {c: {"model": ErrorResponse, "description": _DESCRIPTIONS[c]} for c in codes}
