"""User persistence. Never commits; callers own the transaction."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, Text, and_, cast, exists, func, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.enums import LinkStatus, UserRole, UserStatus
from app.core.errors import ConflictError
from app.models.identity import (
    CoachMembership,
    PlayerProfile,
    Profile,
    TrainerPlayerLink,
    TrainerProfile,
    User,
)


@dataclass(frozen=True, slots=True)
class AuthState:
    status: UserStatus
    role: UserRole
    is_email_verified: bool


@dataclass(frozen=True, slots=True)
class UserFilter:
    role: UserRole | None = None
    status: UserStatus | None = None
    trainer_id: uuid.UUID | None = None
    query: str | None = None


@dataclass(frozen=True, slots=True)
class UserRow:
    """A user joined with their profile and (for trainers/coaches) their tenant."""

    user: User
    profile: Profile
    trainer_id: uuid.UUID | None
    business_name: str | None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _translate_email_conflict(exc: IntegrityError) -> Exception:
    if "uq_users_email" in str(exc.orig):
        return ConflictError("EMAIL_TAKEN", "Email is already in use")
    return exc


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> User:
        """Insert; the CITEXT UNIQUE on email arbitrates concurrent registrations."""
        self._session.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise _translate_email_conflict(exc) from exc
        return user

    async def get_by_id(self, user_id: uuid.UUID, *, for_update: bool = False) -> User | None:
        stmt = select(User).where(User.id == user_id).execution_options(populate_existing=True)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Case-insensitive (``users.email`` is CITEXT)."""
        stmt = select(User).where(User.email == email)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def set_email(self, user: User, email: str) -> None:
        """Change the email; the CITEXT UNIQUE constraint arbitrates races."""
        user.email = email
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise _translate_email_conflict(exc) from exc

    async def get_profile(self, user_id: uuid.UUID) -> Profile | None:
        stmt = select(Profile).where(Profile.user_id == user_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_auth_state(self, user_id: uuid.UUID) -> AuthState | None:
        """One PK lookup used on every authenticated request."""
        row = (
            await self._session.execute(
                select(User.status, User.role, User.is_email_verified).where(User.id == user_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return AuthState(status=row.status, role=row.role, is_email_verified=row.is_email_verified)

    async def list_page(
        self,
        flt: UserFilter,
        cursor: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[UserRow]:
        """Keyset page sorted ``created_at desc, id desc``; returns up to ``limit + 1`` rows."""
        membership = aliased(CoachMembership)
        tenant = aliased(TrainerProfile)
        tenant_id = (
            select(TrainerProfile.user_id)
            .where(TrainerProfile.user_id == User.id)
            .scalar_subquery()
        )
        coach_trainer_id = (
            select(membership.trainer_id)
            .where(membership.coach_user_id == User.id, membership.is_active.is_(True))
            .scalar_subquery()
        )
        trainer_expr = func.coalesce(tenant_id, coach_trainer_id)
        stmt = (
            select(User, Profile, trainer_expr.label("trainer_id"), tenant.business_name)
            .join(Profile, Profile.user_id == User.id)
            .outerjoin(tenant, tenant.user_id == trainer_expr)
        )
        if flt.role is not None:
            stmt = stmt.where(User.role == flt.role)
        if flt.status is not None:
            stmt = stmt.where(User.status == flt.status)
        if flt.trainer_id is not None:
            stmt = stmt.where(self._member_of(flt.trainer_id))
        if flt.query:
            pattern = f"%{_escape_like(flt.query)}%"
            stmt = stmt.where(
                or_(
                    cast(User.email, Text).ilike(pattern, escape="\\"),
                    Profile.first_name.ilike(pattern, escape="\\"),
                    Profile.last_name.ilike(pattern, escape="\\"),
                )
            )
        if cursor is not None:
            stmt = stmt.where(tuple_(User.created_at, User.id) < tuple_(*cursor))
        stmt = stmt.order_by(User.created_at.desc(), User.id.desc()).limit(limit + 1)
        result = await self._session.execute(stmt)
        return [
            UserRow(user=u, profile=p, trainer_id=tid, business_name=bn)
            for u, p, tid, bn in result.all()
        ]

    @staticmethod
    def _member_of(trainer_id: uuid.UUID) -> ColumnElement[bool]:
        """Users belonging to a tenant: the trainer, its coaches, and owners of linked players."""
        is_trainer = User.id == trainer_id
        is_coach = exists().where(
            CoachMembership.coach_user_id == User.id,
            CoachMembership.trainer_id == trainer_id,
            CoachMembership.is_active.is_(True),
        )
        is_player_owner = exists().where(
            and_(
                PlayerProfile.owner_user_id == User.id,
                TrainerPlayerLink.player_profile_id == PlayerProfile.id,
                TrainerPlayerLink.trainer_id == trainer_id,
                TrainerPlayerLink.status == LinkStatus.ACTIVE,
            )
        )
        return or_(is_trainer, is_coach, is_player_owner)
