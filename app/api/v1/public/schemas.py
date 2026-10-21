from typing import Literal

from app.api.schemas import HexColor, ORMModel


class ShareLinkPreview(ORMModel):
    trainer_business_name: str
    trainer_slug: str
    logo_url: str | None
    kind: Literal["player_static", "coach_unique"]
    is_valid: Literal[True] = True


class PublicBranding(ORMModel):
    business_name: str
    logo_url: str | None
    brand_primary_color: HexColor | None
    brand_secondary_color: HexColor | None
