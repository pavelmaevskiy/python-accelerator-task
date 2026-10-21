"""Credentials and token lifecycle: login, refresh, logout, verification, password reset."""

import uuid
from datetime import timedelta

import anyio.to_thread

from app.core.clock import Clock
from app.core.enums import OneTimeTokenKind, UserStatus
from app.core.errors import AuthenticationError, DomainValidationError, PermissionDeniedError
from app.core.password_policy import check_password
from app.core.rate_limit import RateLimiter
from app.core.security import PasswordHasher, generate_opaque_token, hash_token
from app.core.unit_of_work import UnitOfWork
from app.repositories.token_repo import TokenRepo
from app.repositories.user_repo import UserRepo
from app.services.audit_service import AuditService
from app.services.token_issuer import TokenIssuer
from app.services.views import AccountView, IssuedTokens, account_view

LOGIN_EMAIL_LIMIT = (5, 60)  # per email, per minute
RESEND_EMAIL_LIMIT = (3, 3600)
VERIFY_TTL = timedelta(hours=48)
RESET_EMAIL_LIMIT = (5, 3600)
RESET_TTL = timedelta(hours=1)


class LoginResult:
    __slots__ = ("tokens", "user")

    def __init__(self, tokens: IssuedTokens, user: AccountView) -> None:
        self.tokens = tokens
        self.user = user


class AuthService:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserRepo,
        tokens: TokenRepo,
        issuer: TokenIssuer,
        hasher: PasswordHasher,
        clock: Clock,
        rate_limiter: RateLimiter,
        audit: AuditService,
    ) -> None:
        self._uow = uow
        self._users = users
        self._tokens = tokens
        self._issuer = issuer
        self._audit = audit
        self._hasher = hasher
        self._clock = clock
        self._rate_limiter = rate_limiter

    async def login(self, email: str, password: str, *, ip_hash: str, ua_hash: str) -> LoginResult:
        limit, window = LOGIN_EMAIL_LIMIT
        await self._rate_limiter.hit(
            f"login:email:{email.lower()}", limit=limit, window_seconds=window
        )
        async with self._uow:
            user = await self._users.get_by_email(email)
            # Always verify (dummy hash for an unknown user) so failures take the same time.
            ok = await anyio.to_thread.run_sync(
                self._hasher.verify, user.password_hash if user else None, password
            )
            if user is None or not ok or user.status != UserStatus.ACTIVE:
                raise AuthenticationError("INVALID_CREDENTIALS", "Invalid email or password")
            if not user.is_email_verified:
                raise PermissionDeniedError("EMAIL_NOT_VERIFIED", "Email address is not verified")
            profile = await self._users.get_profile(user.id)
            assert profile is not None  # noqa: S101  # 1:1 with users, created together
            user.last_login_at = self._clock.now()
            tokens = await self._issuer.issue_pair(
                user.id, user.role, family_id=None, ip_hash=ip_hash, ua_hash=ua_hash
            )
            return LoginResult(tokens, account_view(user, profile))

    async def refresh(self, token: str, *, ip_hash: str, ua_hash: str) -> IssuedTokens:
        """Rotate a refresh token. Presenting an already-consumed token revokes the family."""
        now = self._clock.now()
        reused: tuple[uuid.UUID, uuid.UUID] | None = None
        async with self._uow:
            row = await self._tokens.get_refresh_by_hash(hash_token(token), for_update=True)
            if row is None or row.revoked_at is not None or row.expires_at <= now:
                raise AuthenticationError("REFRESH_INVALID", "Refresh token is invalid")
            if row.consumed_at is not None:
                await self._tokens.revoke_family(row.family_id, now=now)
                reused = (row.user_id, row.family_id)
            else:
                user = await self._users.get_by_id(row.user_id)
                if user is None or user.status != UserStatus.ACTIVE:
                    raise AuthenticationError("ACCOUNT_INACTIVE", "Account is not active")
                await self._tokens.mark_consumed(row, now=now)
                return await self._issuer.issue_pair(
                    user.id, user.role, family_id=row.family_id, ip_hash=ip_hash, ua_hash=ua_hash
                )
        # Reuse path: the family revocation above is committed; the audit row goes in its own tx.
        assert reused is not None  # noqa: S101
        await self._audit.record_isolated(
            actor_user_id=reused[0],
            action="refresh_reuse",
            target_type="refresh_family",
            target_id=reused[1],
        )
        raise AuthenticationError("REFRESH_REUSE_DETECTED", "Refresh token reuse detected")

    async def logout(self, user_id: uuid.UUID, token: str) -> None:
        """Revoke the token's family if it belongs to the caller. Always succeeds."""
        async with self._uow:
            row = await self._tokens.get_refresh_by_hash(hash_token(token))
            if row is not None and row.user_id == user_id:
                await self._tokens.revoke_family(row.family_id, now=self._clock.now())

    # --- email verification ---------------------------------------------------------------

    async def verify_email(self, token: str) -> None:
        """Consume an ``email_verify`` or ``email_change`` token (unknown/expired/used: 422)."""
        async with self._uow:
            row = await self._tokens.consume_one_time(
                hash_token(token),
                now=self._clock.now(),
                kinds=[OneTimeTokenKind.EMAIL_VERIFY, OneTimeTokenKind.EMAIL_CHANGE],
            )
            if row is None:
                raise DomainValidationError("TOKEN_INVALID", "Token is invalid or expired")
            user = await self._users.get_by_id(row.user_id, for_update=True)
            if user is None or user.status == UserStatus.DELETED:
                raise DomainValidationError("TOKEN_INVALID", "Token is invalid or expired")
            if row.kind == OneTimeTokenKind.EMAIL_CHANGE:
                new_email = (row.payload or {}).get("new_email")
                if not isinstance(new_email, str):
                    raise DomainValidationError("TOKEN_INVALID", "Token is invalid or expired")
                await self._users.set_email(user, new_email)
            user.is_email_verified = True

    async def resend_verification(self, email: str) -> None:
        """Always returns normally (no enumeration); enqueues only for an unverified account."""
        limit, window = RESEND_EMAIL_LIMIT
        await self._rate_limiter.hit(
            f"verify_resend:email:{email.lower()}", limit=limit, window_seconds=window
        )
        async with self._uow:
            user = await self._users.get_by_email(email)
            if user is None or user.is_email_verified or user.status != UserStatus.ACTIVE:
                return
            token = generate_opaque_token()
            now = self._clock.now()
            await self._tokens.issue_one_time(
                user.id,
                OneTimeTokenKind.EMAIL_VERIFY,
                hash_token(token),
                now + VERIFY_TTL,
                now=now,
            )
            self._uow.enqueue("send_verification_email", user_id=str(user.id), token=token)

    # --- password reset -------------------------------------------------------------------

    async def request_password_reset(self, email: str) -> None:
        """Always returns normally (no enumeration)."""
        limit, window = RESET_EMAIL_LIMIT
        await self._rate_limiter.hit(
            f"password_reset:email:{email.lower()}", limit=limit, window_seconds=window
        )
        async with self._uow:
            user = await self._users.get_by_email(email)
            if user is None or user.status == UserStatus.DELETED:
                return
            token = generate_opaque_token()
            now = self._clock.now()
            await self._tokens.issue_one_time(
                user.id,
                OneTimeTokenKind.PASSWORD_RESET,
                hash_token(token),
                now + RESET_TTL,
                now=now,
            )
            self._uow.enqueue("send_password_reset_email", user_id=str(user.id), token=token)

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        """Set a new password from a reset or trainer-setup token; revokes every session."""
        now = self._clock.now()
        async with self._uow:
            row = await self._tokens.consume_one_time(
                hash_token(token),
                now=now,
                kinds=[OneTimeTokenKind.PASSWORD_RESET, OneTimeTokenKind.TRAINER_SETUP],
            )
            if row is None:
                raise DomainValidationError("TOKEN_INVALID", "Token is invalid or expired")
            user = await self._users.get_by_id(row.user_id, for_update=True)
            profile = await self._users.get_profile(row.user_id)
            if user is None or profile is None or user.status == UserStatus.DELETED:
                raise DomainValidationError("TOKEN_INVALID", "Token is invalid or expired")
            inputs = [user.email.split("@")[0], profile.first_name, profile.last_name]
            await anyio.to_thread.run_sync(check_password, new_password, inputs)
            user.password_hash = await anyio.to_thread.run_sync(self._hasher.hash, new_password)
            user.is_email_verified = True  # the token proves mailbox control
            if row.kind == OneTimeTokenKind.TRAINER_SETUP:
                if user.status == UserStatus.PENDING:
                    user.status = UserStatus.ACTIVE
                user.must_change_password = False
            await self._tokens.revoke_all_for_user(user.id, now=now)
