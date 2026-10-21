from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.enums import UserRole, UserStatus
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitedError
from app.core.security import PasswordHasher
from app.services.auth_service import AuthService
from app.services.views import IssuedTokens
from tests.fakes import MutableClock


class FakeUow:
    async def __aenter__(self) -> "FakeUow":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class FakeUsers:
    def __init__(self, user: SimpleNamespace | None) -> None:
        self.user = user

    async def get_by_email(self, email: str) -> SimpleNamespace | None:
        return self.user

    async def get_profile(self, user_id: object) -> SimpleNamespace:
        return SimpleNamespace(first_name="Ann", last_name="Lee")


class FakeIssuer:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def issue_pair(self, user_id: object, role: object, **kw: object) -> IssuedTokens:
        self.calls.append({"user_id": user_id, "role": role, **kw})
        return IssuedTokens("access", "refresh", 900)


class FakeLimiter:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.keys: list[str] = []

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> None:
        self.keys.append(key)
        if self.fail:
            raise RateLimitedError("RATE_LIMITED", "slow", retry_after=5)


class SpyHasher:
    def __init__(self) -> None:
        self.real = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        self.verify_calls: list[str | None] = []

    def verify(self, digest: str | None, password: str) -> bool:
        self.verify_calls.append(digest)
        return self.real.verify(digest, password)


def _user(hasher: SpyHasher, **overrides: object) -> SimpleNamespace:
    base = {
        "id": uuid4(),
        "email": "ann@x.co",
        "password_hash": hasher.real.hash("right-password-123"),
        "role": UserRole.PLAYER,
        "status": UserStatus.ACTIVE,
        "is_email_verified": True,
        "must_change_password": False,
        "last_login_at": None,
    }
    return SimpleNamespace(**{**base, **overrides})


def _service(
    user: SimpleNamespace | None, hasher: SpyHasher, limiter: FakeLimiter | None = None
) -> tuple[AuthService, FakeIssuer, MutableClock]:
    issuer, clock = FakeIssuer(), MutableClock(datetime(2026, 10, 21, tzinfo=UTC))
    service = AuthService(
        FakeUow(),  # type: ignore[arg-type]
        FakeUsers(user),  # type: ignore[arg-type]
        issuer,  # type: ignore[arg-type]
        hasher,  # type: ignore[arg-type]
        clock,
        limiter or FakeLimiter(),  # type: ignore[arg-type]
    )
    return service, issuer, clock


async def test_login_sets_last_login_and_issues_pair_in_new_family() -> None:
    hasher = SpyHasher()
    user = _user(hasher)
    service, issuer, clock = _service(user, hasher)
    result = await service.login("ann@x.co", "right-password-123", ip_hash="i", ua_hash="u")
    assert user.last_login_at == clock.now()
    assert issuer.calls == [
        {
            "user_id": user.id,
            "role": UserRole.PLAYER,
            "family_id": None,
            "ip_hash": "i",
            "ua_hash": "u",
        }
    ]
    assert result.tokens.access_token == "access" and result.user.first_name == "Ann"


async def test_hasher_is_invoked_with_none_when_user_is_unknown() -> None:
    hasher = SpyHasher()
    service, issuer, _ = _service(None, hasher)
    with pytest.raises(AuthenticationError) as e:
        await service.login("ghost@x.co", "whatever-long-password", ip_hash="i", ua_hash="u")
    assert e.value.code == "INVALID_CREDENTIALS"
    assert hasher.verify_calls == [None]
    assert issuer.calls == []


@pytest.mark.parametrize("status", [UserStatus.INACTIVE, UserStatus.DELETED, UserStatus.PENDING])
async def test_non_active_user_with_correct_password_is_uniform_401(status) -> None:
    hasher = SpyHasher()
    service, issuer, _ = _service(_user(hasher, status=status), hasher)
    with pytest.raises(AuthenticationError) as e:
        await service.login("ann@x.co", "right-password-123", ip_hash="i", ua_hash="u")
    assert e.value.code == "INVALID_CREDENTIALS" and issuer.calls == []


async def test_unverified_user_with_correct_password_gets_403_and_no_tokens() -> None:
    hasher = SpyHasher()
    service, issuer, _ = _service(_user(hasher, is_email_verified=False), hasher)
    with pytest.raises(PermissionDeniedError) as e:
        await service.login("ann@x.co", "right-password-123", ip_hash="i", ua_hash="u")
    assert e.value.code == "EMAIL_NOT_VERIFIED" and issuer.calls == []


async def test_unverified_user_with_wrong_password_is_401_not_403() -> None:
    hasher = SpyHasher()
    service, _, _ = _service(_user(hasher, is_email_verified=False), hasher)
    with pytest.raises(AuthenticationError):
        await service.login("ann@x.co", "wrong-password-123", ip_hash="i", ua_hash="u")


async def test_email_rate_limit_key_is_lowercased_and_precedes_credentials_check() -> None:
    hasher = SpyHasher()
    limiter = FakeLimiter(fail=True)
    service, _, _ = _service(_user(hasher), hasher, limiter)
    with pytest.raises(RateLimitedError):
        await service.login("Ann@X.co", "right-password-123", ip_hash="i", ua_hash="u")
    assert limiter.keys == ["login:email:ann@x.co"] and hasher.verify_calls == []
