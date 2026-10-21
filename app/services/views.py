"""Plain data views returned by services (no ORM objects cross into the API layer)."""

import uuid
from dataclasses import dataclass

from app.core.enums import UserRole, UserStatus
from app.models.identity import Profile, User


@dataclass(frozen=True, slots=True)
class AccountView:
    id: uuid.UUID
    email: str
    role: UserRole
    status: UserStatus
    first_name: str
    last_name: str
    is_email_verified: bool
    must_change_password: bool


def account_view(user: User, profile: Profile) -> AccountView:
    return AccountView(
        id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        first_name=profile.first_name,
        last_name=profile.last_name,
        is_email_verified=user.is_email_verified,
        must_change_password=user.must_change_password,
    )


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
