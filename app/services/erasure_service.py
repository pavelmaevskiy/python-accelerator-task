"""GDPR erasure by in-place anonymisation (the ``users`` row is never deleted).

Runs in one transaction, following specs/architect-architecture.md "Erasure (GDPR)":
lock, commitment check, anonymise PII, revoke sessions/tokens, ledger row, audit row, then
(after commit) delete the user's stored media.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date

import anyio.to_thread
from redis.asyncio import Redis

from app.core.clock import Clock
from app.core.commitments import CommitmentChecker
from app.core.enums import UserRole, UserStatus
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.principal import Principal
from app.core.security import PasswordHasher, generate_opaque_token
from app.core.unit_of_work import UnitOfWork
from app.models.identity import User, UserErasure
from app.repositories.erasure_repo import ErasureRepo
from app.repositories.impersonation_repo import ImpersonationRepo
from app.repositories.token_repo import TokenRepo
from app.repositories.user_repo import UserRepo
from app.services.audit_service import AuditService
from app.services.branding_service import public_branding_key


@dataclass(frozen=True, slots=True)
class EraseInput:
    reason: str
    legal_retention_until: date | None


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


class ErasureService:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserRepo,
        erasure: ErasureRepo,
        tokens: TokenRepo,
        impersonations: ImpersonationRepo,
        audit: AuditService,
        commitments: CommitmentChecker,
        hasher: PasswordHasher,
        clock: Clock,
        redis: Redis,
    ) -> None:
        self._uow = uow
        self._users = users
        self._erasure = erasure
        self._tokens = tokens
        self._impersonations = impersonations
        self._audit = audit
        self._commitments = commitments
        self._hasher = hasher
        self._clock = clock
        self._redis = redis

    async def erase_user(self, admin: Principal, user_id: uuid.UUID, data: EraseInput) -> None:
        unusable = await anyio.to_thread.run_sync(self._hasher.hash, generate_opaque_token())
        media: list[str] = []
        stale_cache: list[str] = []
        async with self._uow:
            user = await self._users.get_by_id(user_id, for_update=True)  # 1. lock
            if user is None:
                raise NotFoundError("NOT_FOUND", "User not found")
            if user.status == UserStatus.DELETED:
                raise ConflictError("ALREADY_ERASED", "This user has already been erased")
            self._guard_target(admin, user)
            dependents = await self._erasure.player_profiles_of(user.id)
            await self._check_commitments([p.id for p in dependents])  # 2. refuse if any exist
            original_email = user.email
            await self._anonymise(user, unusable, media, stale_cache)  # 3-4.
            # A parent's dependents (and their logins) go with them; a child login takes only its
            # own profile (owned by the parent, who is not erased).
            await self._erasure.deactivate_links([p.id for p in dependents])
            for profile in dependents:
                self._erasure.anonymise_player_profile(profile)
                if profile.login_user_id is not None and profile.login_user_id != user.id:
                    child = await self._users.get_by_id(profile.login_user_id, for_update=True)
                    if child is not None and child.status != UserStatus.DELETED:
                        await self._anonymise(child, unusable, media, stale_cache)
            await self._erasure.expire_pending_approvals(user.id, now=self._clock.now())
            await self._erasure.add_ledger_row(  # 5.
                UserErasure(
                    original_user_id=user.id,
                    original_email_hash=email_hash(original_email),
                    erased_by_user_id=admin.user_id,
                    reason=data.reason,
                    legal_retention_until=data.legal_retention_until,
                )
            )
            await self._audit.record(  # 6.
                actor_user_id=admin.user_id,
                action="user.erased",
                target_type="user",
                target_id=user.id,
            )
            for key in media:  # 7. after commit
                self._uow.enqueue("delete_s3_object", key=key)
        for key in stale_cache:
            await self._redis.delete(key)

    @staticmethod
    def _guard_target(admin: Principal, user: User) -> None:
        """Erasure is irreversible: never on oneself or on another super-admin."""
        if user.id == admin.user_id:
            raise ConflictError("CANNOT_TARGET_SELF", "You cannot erase your own account")
        if user.role == UserRole.SUPER_ADMIN:
            raise PermissionDeniedError("CANNOT_MODIFY_ADMIN", "Super admins cannot be erased")

    async def _check_commitments(self, profile_ids: list[uuid.UUID]) -> None:
        blocking: list[uuid.UUID] = []
        for profile_id in profile_ids:
            blocking += await self._commitments.active_for_player(profile_id, None)
        if blocking:
            raise ConflictError(
                "ACTIVE_COMMITMENTS",
                "User has active commitments",
                details={"rsvp_ids": [str(i) for i in blocking]},
            )

    async def _anonymise(
        self, user: User, unusable_hash: str, media: list[str], stale_cache: list[str]
    ) -> None:
        now = self._clock.now()
        original_email = user.email
        user.email = f"erased+{user.id}@invalid"
        user.password_hash = unusable_hash
        user.status = UserStatus.DELETED
        user.deleted_at = now
        user.is_email_verified = False
        user.must_change_password = False
        avatar = await self._erasure.anonymise_profile(user.id)
        if avatar:
            media.append(avatar)
        if user.role == UserRole.TRAINER:
            trainer = await self._erasure.anonymise_trainer_profile(user.id)
            if trainer is not None:
                stale_cache.append(public_branding_key(trainer[0]))
                if trainer[1]:
                    media.append(trainer[1])
        elif user.role == UserRole.COACH:
            await self._erasure.anonymise_coach(user.id, now=now)
        await self._tokens.revoke_all_for_user(user.id, now=now)
        await self._erasure.scrub_token_payloads(user.id)
        await self._tokens.consume_all_one_time_for_user(user.id, now=now)
        await self._impersonations.end_all_for_user(user.id, now=now)
        await self._erasure.scrub_free_text(user.id)
        await self._erasure.scrub_share_links(user.id, original_email)
