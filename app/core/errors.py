"""Domain exceptions. Services raise these; the API layer maps them to the error envelope."""

from typing import Any, ClassVar


class DomainError(Exception):
    status_code: ClassVar[int] = 400

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class AuthenticationError(DomainError):
    status_code = 401


class PermissionDeniedError(DomainError):
    status_code = 403


class NotFoundError(DomainError):
    status_code = 404


class ConflictError(DomainError):
    status_code = 409


class DomainValidationError(DomainError):
    status_code = 422


class RateLimitedError(DomainError):
    status_code = 429

    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        retry_after: int,
    ) -> None:
        super().__init__(code, message, details)
        self.retry_after = retry_after
