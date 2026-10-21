from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import EmailStr, Field

from app.api.schemas import CursorParams, ORMModel, Slug, StrictModel
from app.api.v1.auth.schemas import UserSummary
from app.core.enums import UserRole, UserStatus

PHONE_PATTERN = r"^\+[1-9]\d{6,14}$"


class AdminUserResponse(ORMModel):
    id: UUID
    email: str  # plain str: erased users carry an ``erased+<id>@invalid`` placeholder
    role: UserRole
    status: UserStatus
    first_name: str
    last_name: str
    phone: str | None
    is_email_verified: bool
    must_change_password: bool
    trainer_id: UUID | None
    business_name: str | None
    last_login_at: datetime | None
    created_at: datetime
    deleted_at: datetime | None


class AdminUserFilter(CursorParams):
    role: UserRole | None = None
    status: UserStatus | None = None
    trainer: UUID | None = None  # members of this tenant
    query: str | None = Field(None, min_length=2, max_length=100)


class AdminUpdateUser(StrictModel):
    """Role and status change only through the dedicated actions."""

    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    phone: str | None = Field(None, pattern=PHONE_PATTERN)
    is_email_verified: bool | None = None
    must_change_password: bool | None = None


class ProvisionTrainer(StrictModel):
    email: EmailStr = Field(max_length=255)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(None, pattern=PHONE_PATTERN)
    business_name: str = Field(min_length=1, max_length=255)
    slug: Slug


class ProvisionTrainerResponse(ORMModel):
    id: UUID
    email: EmailStr
    business_name: str
    slug: str
    status: Literal["pending"]


class ImpersonateRequest(StrictModel):
    reason: str | None = Field(None, max_length=500)


class ImpersonateResponse(ORMModel):
    """Access token only: by decision O4/D3 no refresh token is issued for impersonation."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105
    expires_in: int  # seconds; the session is capped at 60 minutes
    expires_at: datetime
    target: UserSummary
