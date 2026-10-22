"""Exception handlers producing the single error envelope {error:{code,message,details}}."""

import http
import logging
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import DomainError, RateLimitedError

logger = logging.getLogger(__name__)


def _envelope(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
        headers=headers,
    )


async def _domain_error(_: Request, e: Exception) -> JSONResponse:
    exc = cast(DomainError, e)
    headers = None
    if isinstance(exc, RateLimitedError):
        headers = {"Retry-After": str(exc.retry_after)}
    return _envelope(exc.status_code, exc.code, exc.message, exc.details, headers)


async def _validation_error(_: Request, e: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, e)
    errors = [
        {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in exc.errors()
    ]
    return _envelope(422, "VALIDATION_ERROR", "Request validation failed", {"errors": errors})


async def _http_error(_: Request, e: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, e)
    code = http.HTTPStatus(exc.status_code).name  # e.g. NOT_FOUND, METHOD_NOT_ALLOWED
    return _envelope(exc.status_code, code, str(exc.detail), None, dict(exc.headers or {}))


async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception", exc_info=exc)
    return _envelope(500, "INTERNAL_ERROR", "Internal server error")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled)
