from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AnyHttpUrl, EmailStr, Field, model_validator

from app.api.schemas import CursorParams, HexColor, ORMModel, StrictModel
from app.core.enums import ShareLinkKind


class ShareLinkResponse(ORMModel):
    id: UUID
    code: str
    url: AnyHttpUrl
    kind: Literal["player_static", "coach_unique"]
    target_email: EmailStr | None
    expires_at: datetime | None
    max_uses: int | None
    use_count: int
    is_active: bool
    created_at: datetime


class CreateShareLink(StrictModel):
    kind: Literal["player_static", "coach_unique"]
    target_email: EmailStr | None = None  # required for coach_unique, forbidden for player_static

    @model_validator(mode="after")
    def _kind_rules(self) -> Self:
        if self.kind == "coach_unique" and self.target_email is None:
            raise ValueError("target_email is required for coach_unique links")
        if self.kind == "player_static" and self.target_email is not None:
            raise ValueError("target_email is not allowed for player_static links")
        return self


class ShareLinkListParams(CursorParams):
    kind: ShareLinkKind | None = None
    is_active: bool | None = None


class BrandingResponse(ORMModel):
    business_name: str
    slug: str
    logo_url: str | None
    brand_primary_color: HexColor | None
    brand_secondary_color: HexColor | None
    address: str | None
    website: AnyHttpUrl | None
    description: str | None


class UpdateBranding(StrictModel):
    business_name: str | None = Field(None, min_length=1, max_length=255)
    address: str | None = Field(None, max_length=500)
    website: AnyHttpUrl | None = None
    description: str | None = Field(None, max_length=2000)
    brand_primary_color: HexColor | None = None
    brand_secondary_color: HexColor | None = None
    logo_key: str | None = Field(None, max_length=255)  # confirm phase of the logo upload
