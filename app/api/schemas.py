"""Shared base schemas and annotated types used by every API module."""

import zoneinfo
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for every request model: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


class ORMModel(BaseModel):
    """Base for every response model."""

    model_config = ConfigDict(from_attributes=True)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, object] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None
    has_more: bool


class CursorParams(BaseModel):
    limit: int = Field(50, ge=1, le=200)
    cursor: str | None = None


def validate_iana_tz(value: str) -> str:
    if value not in zoneinfo.available_timezones():
        raise ValueError(f"unknown IANA timezone: {value!r}")
    return value


Password = Annotated[str, Field(min_length=12, max_length=128)]
HexColor = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
Timezone = Annotated[str, AfterValidator(validate_iana_tz)]
Slug = Annotated[str, Field(min_length=3, max_length=63, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
