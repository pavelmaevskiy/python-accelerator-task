"""Presign schemas shared by the avatar (me) and logo (trainer branding) flows."""

from typing import Literal

from pydantic import AnyHttpUrl, Field

from app.api.schemas import ORMModel, StrictModel

MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_LOGO_BYTES = 2 * 1024 * 1024


class PresignRequest(StrictModel):
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    content_length: int = Field(gt=0, le=MAX_AVATAR_BYTES)


class LogoPresignRequest(StrictModel):
    content_type: Literal["image/png", "image/svg+xml", "image/webp"]
    content_length: int = Field(gt=0, le=MAX_LOGO_BYTES)


class PresignResponse(ORMModel):
    upload_url: AnyHttpUrl
    upload_headers: dict[str, str]
    key: str  # quarantine/<owner>/<id>; echo it back as avatar_key / logo_key
    expires_in: int = 300
