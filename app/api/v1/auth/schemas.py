from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field

from app.api.schemas import ORMModel, StrictModel
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
