from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field

from app.api.schemas import ORMModel, Password, StrictModel, Timezone
from app.core.enums import UserRole, UserStatus


class TokenPair(ORMModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int = 900


class UserSummary(ORMModel):
    id: UUID
    email: EmailStr
    role: UserRole
    status: UserStatus
    first_name: str
    last_name: str
    is_email_verified: bool
    must_change_password: bool


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(TokenPair):
    user: UserSummary


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=256)


class LogoutRequest(StrictModel):
    refresh_token: str = Field(max_length=256)


class VerifyEmailRequest(StrictModel):
    token: str = Field(min_length=20, max_length=256)


class ResendVerificationRequest(StrictModel):
    email: EmailStr


class PasswordResetRequest(StrictModel):
    email: EmailStr


class PasswordResetConfirm(StrictModel):
    token: str = Field(min_length=20, max_length=256)
    new_password: Password


class RegisterRequest(StrictModel):
    email: EmailStr = Field(max_length=255)
    password: Password
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(None, pattern=r"^\+[1-9]\d{6,14}$")
    timezone: Timezone = "UTC"
    birth_date: date | None = Field(None, gt=date(1900, 1, 1))  # own player profile
    # Existing dependents to link (authenticated caller only); coach links ignore both fields.
    player_profile_ids: list[UUID] | None = Field(None, min_length=1, max_length=20)


class RegisterResponse(ORMModel):
    user: UserSummary
    tokens: TokenPair | None  # null unless the flow proves mailbox control (D1)
    verification_required: bool
    linked_trainer_id: UUID
