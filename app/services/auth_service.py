"""Credentials and token lifecycle: login, refresh, logout, verification, password reset."""

import anyio.to_thread

from app.core.clock import Clock
from app.core.enums import UserStatus
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.rate_limit import RateLimiter
from app.core.security import PasswordHasher
from app.core.unit_of_work import UnitOfWork
from app.repositories.user_repo import UserRepo
from app.services.token_issuer import TokenIssuer
from app.services.views import AccountView, IssuedTokens, account_view

LOGIN_EMAIL_LIMIT = (5, 60)  # per email, per minute


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
        issuer: TokenIssuer,
        hasher: PasswordHasher,
        clock: Clock,
        rate_limiter: RateLimiter,
    ) -> None:
        self._uow = uow
        self._users = users
        self._issuer = issuer
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
