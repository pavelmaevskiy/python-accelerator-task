from datetime import time
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import EmailStr, Field, model_validator

from app.api.schemas import ORMModel, StrictModel, Timezone
from app.core.enums import UserRole, UserStatus


class TrainerProfileResponse(ORMModel):
    business_name: str
    slug: str
    logo_url: str | None
    brand_primary_color: str | None
    brand_secondary_color: str | None
    address: str | None
    website: str | None
    description: str | None


class CoachProfileResponse(ORMModel):
    bio: str | None
    credentials: str | None
    certifications: list[Any]
    is_public: bool
    trainer_id: UUID | None


class ProfileResponse(ORMModel):
    id: UUID
    email: EmailStr
    role: UserRole
    status: UserStatus
    first_name: str
    last_name: str
    phone: str | None
    school: str | None
    timezone: str
    avatar_url: str | None
    is_email_verified: bool
    must_change_password: bool
    is_child: bool
    trainer_id: UUID | None  # resolved context: trainer/coach own, player = selected
    trainer_profile: TrainerProfileResponse | None
    coach_profile: CoachProfileResponse | None


class UpdateProfile(StrictModel):
    """role/email/status are deliberately absent, so attempting them is a 422."""

    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, pattern=r"^\+[1-9]\d{6,14}$")
    school: str | None = Field(None, max_length=255)
    timezone: Timezone | None = None
    avatar_key: str | None = Field(None, max_length=255)  # confirm phase of PUT /me/avatar


class EmailChangeRequest(StrictModel):
    new_email: EmailStr = Field(max_length=255)
    current_password: str = Field(min_length=1, max_length=128)


class AvailabilityWindowIn(StrictModel):
    weekday: int = Field(ge=0, le=6)
    start_time: time
    end_time: time
    is_available: bool = True

    @model_validator(mode="after")
    def _start_before_end(self) -> Self:
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class ReplaceAvailability(StrictModel):
    windows: list[AvailabilityWindowIn] = Field(max_length=100)


class AvailabilityWindowOut(ORMModel):
    id: UUID
    weekday: int
    start_time: time
    end_time: time
    is_available: bool


class AvailabilityResponse(ORMModel):
    subject_type: Literal["player", "coach"]
    subject_id: UUID
    windows: list[AvailabilityWindowOut]
