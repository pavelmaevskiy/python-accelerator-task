from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AnyHttpUrl, EmailStr, model_validator

from app.api.schemas import CursorParams, ORMModel, StrictModel
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
