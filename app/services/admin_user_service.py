"""Super-admin user management: directory, edits, deactivate/reactivate. Audited."""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import anyio.to_thread

from app.core.clock import Clock
from app.core.enums import OneTimeTokenKind, UserRole, UserStatus
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.pagination import PageResult, build_page, decode_cursor
from app.core.principal import Principal
from app.core.security import PasswordHasher, generate_opaque_token, hash_token
from app.core.unit_of_work import UnitOfWork
from app.models.identity import Profile, TrainerProfile, User
from app.repositories.impersonation_repo import ImpersonationRepo
from app.repositories.profile_repo import ProfileRepo
from app.repositories.token_repo import TokenRepo
from app.repositories.user_repo import UserFilter, UserRepo, UserRow
from app.services.audit_service import AuditService

TRAINER_SETUP_TTL = timedelta(days=7)  # architecture C3 (binding) over the epic's 24 h (O3)
_EDITABLE_PROFILE = ("first_name", "last_name", "phone")
_EDITABLE_USER = ("is_email_verified", "must_change_password")


@dataclass(frozen=True, slots=True)
class AdminUserView:
    id: uuid.UUID
    email: str
    role: UserRole
    status: UserStatus
    first_name: str
    last_name: str
    phone: str | None
    is_email_verified: bool
    must_change_password: bool
    trainer_id: uuid.UUID | None
    business_name: str | None
    last_login_at: datetime | None
    created_at: datetime
    deleted_at: datetime | None


def admin_view(row: UserRow) -> AdminUserView:
    u, p = row.user, row.profile
    return AdminUserView(
        id=u.id,
        email=u.email,
        role=u.role,
        status=u.status,
        first_name=p.first_name,
        last_name=p.last_name,
        phone=p.phone,
        is_email_verified=u.is_email_verified,
        must_change_password=u.must_change_password,
        trainer_id=row.trainer_id,
        business_name=row.business_name,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
        deleted_at=u.deleted_at,
    )


@dataclass(frozen=True, slots=True)
class ProvisionTrainerInput:
    email: str
    first_name: str
    last_name: str
    phone: str | None
    business_name: str
    slug: str


@dataclass(frozen=True, slots=True)
class ProvisionedTrainerView:
    id: uuid.UUID
    email: str
    business_name: str
    slug: str
    status: UserStatus


class AdminUserService:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserRepo,
        profiles: ProfileRepo,
        tokens: TokenRepo,
        impersonations: ImpersonationRepo,
        audit: AuditService,
        hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._users = users
        self._profiles = profiles
        self._tokens = tokens
        self._impersonations = impersonations
        self._audit = audit
        self._hasher = hasher
        self._clock = clock

    async def _row(self, user_id: uuid.UUID) -> UserRow:
        row = await self._users.get_row(user_id)
        if row is None:
            raise NotFoundError("NOT_FOUND", "User not found")
        return row

    @staticmethod
    def _reject_erased(row: UserRow) -> None:
        if row.user.status == UserStatus.DELETED:
            raise ConflictError("USER_ERASED", "This user has been erased")

    @staticmethod
    def _guard_target(admin: Principal, row: UserRow) -> None:
        """Deactivation is never allowed on oneself or on another super-admin."""
        if row.user.id == admin.user_id:
            raise ConflictError("CANNOT_TARGET_SELF", "You cannot deactivate your own account")
        if row.user.role == UserRole.SUPER_ADMIN:
            raise PermissionDeniedError("CANNOT_MODIFY_ADMIN", "Super admins cannot be deactivated")

    async def list(
        self,
        flt: UserFilter,
        *,
        cursor: str | None,
        limit: int,
    ) -> PageResult[AdminUserView]:
        decoded = decode_cursor(cursor) if cursor else None
        async with self._uow:
            rows = await self._users.list_page(flt, decoded, limit)
            page = build_page(rows, limit, lambda r: (r.user.created_at, r.user.id))
            return PageResult([admin_view(r) for r in page.items], page.next_cursor, page.has_more)

    async def get(self, user_id: uuid.UUID) -> AdminUserView:
        async with self._uow:
            return admin_view(await self._row(user_id))

    async def update(
        self, admin: Principal, user_id: uuid.UUID, changes: dict[str, object]
    ) -> AdminUserView:
        async with self._uow:
            row = await self._row(user_id)
            self._reject_erased(row)
            for name in _EDITABLE_PROFILE:
                if name in changes and (changes[name] is not None or name == "phone"):
                    setattr(row.profile, name, changes[name])
            for name in _EDITABLE_USER:
                if changes.get(name) is not None:
                    setattr(row.user, name, bool(changes[name]))
            await self._audit.record(
                actor_user_id=admin.user_id,
                action="user.updated",
                target_type="user",
                target_id=user_id,
                details={"fields": sorted(changes)},  # field names only, never values (PII)
            )
            return admin_view(row)

    async def deactivate(self, admin: Principal, user_id: uuid.UUID) -> AdminUserView:
        """Idempotent. Access tokens die on the next request (state is re-read per request)."""
        async with self._uow:
            row = await self._row(user_id)
            self._reject_erased(row)
            self._guard_target(admin, row)
            if row.user.status != UserStatus.INACTIVE:
                now = self._clock.now()
                row.user.status = UserStatus.INACTIVE
                await self._tokens.revoke_all_for_user(user_id, now=now)
                # Sessions where the user is the target or the impersonating admin die with it.
                await self._impersonations.end_all_for_user(user_id, now=now)
                await self._audit.record(
                    actor_user_id=admin.user_id,
                    action="user.deactivated",
                    target_type="user",
                    target_id=user_id,
                )
            return admin_view(row)

    async def reactivate(self, admin: Principal, user_id: uuid.UUID) -> AdminUserView:
        async with self._uow:
            row = await self._row(user_id)
            self._reject_erased(row)  # a deleted (erased) user can never be restored
            if row.user.status == UserStatus.PENDING:
                # PENDING -> ACTIVE would skip the trainer setup flow (password never set).
                raise ConflictError("USER_PENDING", "This user has not completed account setup")
            if row.user.status != UserStatus.ACTIVE:
                row.user.status = UserStatus.ACTIVE
                await self._audit.record(
                    actor_user_id=admin.user_id,
                    action="user.reactivated",
                    target_type="user",
                    target_id=user_id,
                )
            return admin_view(row)

    async def provision_trainer(
        self, admin: Principal, data: ProvisionTrainerInput
    ) -> ProvisionedTrainerView:
        """One transaction: user + profile + trainer profile + setup token + audit + invite."""
        # A random, never-returned password: the trainer sets a real one via the setup link.
        unusable = await anyio.to_thread.run_sync(self._hasher.hash, generate_opaque_token())
        token = generate_opaque_token()
        now = self._clock.now()
        async with self._uow:
            user = await self._users.add(
                User(
                    email=data.email,
                    password_hash=unusable,
                    role=UserRole.TRAINER,
                    status=UserStatus.PENDING,
                    must_change_password=True,
                )
            )
            await self._profiles.add_profile(
                Profile(
                    user_id=user.id,
                    first_name=data.first_name,
                    last_name=data.last_name,
                    phone=data.phone,
                )
            )
            await self._profiles.add_trainer_profile(
                TrainerProfile(user_id=user.id, business_name=data.business_name, slug=data.slug)
            )
            await self._tokens.issue_one_time(
                user.id,
                OneTimeTokenKind.TRAINER_SETUP,
                hash_token(token),
                now + TRAINER_SETUP_TTL,
                now=now,
            )
            await self._audit.record(
                actor_user_id=admin.user_id,
                action="trainer.created",
                target_type="user",
                target_id=user.id,
                details={"slug": data.slug},
            )
            self._uow.enqueue("send_trainer_invite_email", user_id=str(user.id), token=token)
            return ProvisionedTrainerView(
                id=user.id,
                email=user.email,
                business_name=data.business_name,
                slug=data.slug,
                status=user.status,
            )
