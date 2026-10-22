from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field

from app.api.schemas import ORMModel, StrictModel
from app.core.enums import Gender, SkillLevel

PHONE_PATTERN = r"^\+[1-9]\d{6,14}$"


class TrainerContext(ORMModel):
    trainer_id: UUID
    business_name: str
    slug: str
    logo_url: str | None
    brand_primary_color: str | None
    player_profile_id: UUID
    linked_at: datetime


class ContextsResponse(ORMModel):
    items: list[TrainerContext]  # active links only; bounded by links per account


class PlayerLinkOut(ORMModel):
    trainer_id: UUID
    business_name: str
    status: Literal["active", "inactive"]
    linked_at: datetime


class PlayerResponse(ORMModel):
    id: UUID
    display_name: str
    birth_date: date
    gender: Gender
    skill_level: SkillLevel | None
    school: str | None
    jersey_number: int | None
    is_dependent: bool
    has_login: bool
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    token_spend_requires_approval: bool
    trainers: list[PlayerLinkOut]


class CreatePlayer(StrictModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "display_name": "Sam",
                    "birth_date": "2015-03-04",
                    "gender": "male",
                    "trainer_ids": [],
                }
            ]
        }
    )

    display_name: str = Field(min_length=1, max_length=100)
    birth_date: date = Field(gt=date(1900, 1, 1))
    gender: Gender
    school: str | None = Field(None, max_length=255)
    jersey_number: int | None = Field(None, ge=0, le=999)
    emergency_contact_name: str | None = Field(None, max_length=100)
    emergency_contact_phone: str | None = Field(None, pattern=PHONE_PATTERN)
    trainer_ids: list[UUID] = Field(default_factory=list, max_length=20)


class UpdatePlayer(StrictModel):
    """``skill_level`` is trainer-owned (Epic-03) and deliberately absent: sending it is a 422."""

    display_name: str | None = Field(None, min_length=1, max_length=100)
    birth_date: date | None = Field(None, gt=date(1900, 1, 1))
    gender: Gender | None = None
    school: str | None = Field(None, max_length=255)
    jersey_number: int | None = Field(None, ge=0, le=999)
    emergency_contact_name: str | None = Field(None, max_length=100)
    emergency_contact_phone: str | None = Field(None, pattern=PHONE_PATTERN)
    token_spend_requires_approval: bool | None = None  # parent only


class AddPlayerTrainer(StrictModel):
    trainer_id: UUID
