"""Audited, time-boxed admin impersonation.

Decision O4 (confirmed by the product owner): **no refresh token is ever issued** for an
impersonation session. The access token itself is valid for the whole 60-minute session and is
revocable at any time because every request re-checks the session row (``exit`` or expiry).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.enums import UserRole, UserStatus
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.principal import Principal
from app.core.security import TokenCodec
from app.core.unit_of_work import JobQueue, UnitOfWork
from app.models.identity import ImpersonationSession
from app.repositories.audit_repo import AuditRepo
from app.repositories.impersonation_repo import ImpersonationRepo
from app.repositories.user_repo import UserRepo
from app.services.audit_service import AuditService
from app.services.views import AccountView, account_view

IMPERSONATION_TTL = timedelta(minutes=60)


@dataclass(frozen=True, slots=True)
class ImpersonationResult:
    access_token: str
    expires_in: int
    expires_at: datetime
    target: AccountView


class ImpersonationService:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserRepo,
        sessions: ImpersonationRepo,
        audit: AuditService,
        codec: TokenCodec,
        clock: Clock,
    ) -> None:
        self._uow = uow
        self._users = users
        self._sessions = sessions
        self._audit = audit
        self._codec = codec
        self._clock = clock

    async def start(
        self, admin: Principal, target_id: uuid.UUID, reason: str | None
    ) -> ImpersonationResult:
        if admin.is_impersonated:
            raise PermissionDeniedError(
                "ADMIN_FORBIDDEN_WHILE_IMPERSONATING", "Not available while impersonating"
            )
        now = self._clock.now()
        async with self._uow:
            target = await self._users.get_by_id(target_id)
            profile = await self._users.get_profile(target_id)
            if target is None or profile is None:
                raise NotFoundError("NOT_FOUND", "User not found")
            if target.role == UserRole.SUPER_ADMIN:
                raise PermissionDeniedError(
                    "CANNOT_IMPERSONATE_ADMIN", "Super admins cannot be impersonated"
                )
            if target.status == UserStatus.DELETED:
                raise ConflictError("USER_ERASED", "This user has been erased")
            if target.status != UserStatus.ACTIVE:
                raise ConflictError("USER_INACTIVE", "This user is not active")
            jti = str(uuid.uuid4())
            expires_at = now + IMPERSONATION_TTL
            await self._sessions.add(
                ImpersonationSession(
                    admin_user_id=admin.user_id,
                    target_user_id=target.id,
                    started_at=now,
                    expires_at=expires_at,
                    reason=reason,
                    jti=jti,
                )
            )
            await self._audit.record(
                actor_user_id=admin.user_id,
                action="impersonation.started",
                target_type="user",
                target_id=target.id,
                details={"jti": jti},
            )
            token = self._codec.encode_access(
                sub=str(target.id),
                role=target.role.value,
                trainer_id=None,
                ttl=IMPERSONATION_TTL,
                act=str(admin.user_id),
                jti=jti,
            )
            return ImpersonationResult(
                access_token=token,
                expires_in=int(IMPERSONATION_TTL.total_seconds()),
                expires_at=expires_at,
                target=account_view(target, profile),
            )

    async def exit(self, principal: Principal) -> None:
        """End the session behind the caller's token; the ``jti`` is dead from now on."""
        if principal.act is None or principal.jti is None:
            raise PermissionDeniedError("NOT_IMPERSONATING", "This is not an impersonation token")
        async with self._uow:
            await self._sessions.end(principal.jti, now=self._clock.now())
            await self._audit.record(
                actor_user_id=principal.act,
                action="impersonation.ended",
                target_type="user",
                target_id=principal.user_id,
                details={"jti": principal.jti},
            )

    async def expire_due(self) -> int:
        """Cron body: close sessions past ``expires_at`` (set-based, idempotent)."""
        async with self._uow:
            return await self._sessions.end_expired(now=self._clock.now())


def make_impersonation_service(
    session: AsyncSession,
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: JobQueue,
    clock: Clock,
    codec: TokenCodec,
) -> ImpersonationService:
    """Factory for workers: jobs may use services but never repositories directly."""
    return ImpersonationService(
        UnitOfWork(session, queue),
        UserRepo(session),
        ImpersonationRepo(session),
        AuditService(sessionmaker, AuditRepo(session)),
        codec,
        clock,
    )
