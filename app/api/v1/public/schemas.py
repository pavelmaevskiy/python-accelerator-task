from typing import Literal

from app.api.schemas import ORMModel


class ShareLinkPreview(ORMModel):
    trainer_business_name: str
    trainer_slug: str
    logo_url: str | None
    kind: Literal["player_static", "coach_unique"]
    is_valid: Literal[True] = True
