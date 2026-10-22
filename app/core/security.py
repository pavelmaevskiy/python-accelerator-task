"""Password hashing, JWT access tokens and opaque tokens.

Callers run ``PasswordHasher.verify`` / ``hash`` through ``anyio.to_thread.run_sync`` so the
event loop is never blocked by argon2.
"""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import argon2
import jwt
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.errors import AuthenticationError


class PasswordHasher:
    def __init__(self, time_cost: int = 3, memory_cost: int = 65536, parallelism: int = 4) -> None:
        self.time_cost = time_cost
        self.memory_cost = memory_cost
        self.parallelism = parallelism
        self._ph = argon2.PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost,
            parallelism=parallelism,
            type=argon2.Type.ID,
        )
        self._dummy = self._ph.hash(secrets.token_urlsafe(16))

    def hash(self, password: str) -> str:
        return self._ph.hash(password)

    def verify(self, digest: str | None, password: str) -> bool:
        """Constant-time-ish verify: a missing digest is checked against a dummy hash."""
        target = digest if digest is not None else self._dummy
        try:
            ok = self._ph.verify(target, password)
        except (VerificationError, InvalidHashError):
            return False
        return ok and digest is not None

    def needs_rehash(self, digest: str) -> bool:
        return self._ph.check_needs_rehash(digest)


class TokenCodec:
    def __init__(self, keys: dict[str, str], current_kid: str) -> None:
        self._keys = keys
        self._kid = current_kid

    def encode_access(
        self,
        sub: str,
        role: str,
        trainer_id: str | None,
        ttl: timedelta,
        act: str | None = None,
        jti: str | None = None,
    ) -> str:
        now = datetime.now(UTC)
        claims: dict[str, Any] = {
            "sub": sub,
            "role": role,
            "trainer_id": trainer_id,
            "act": act,
            "jti": jti or str(uuid.uuid4()),
            "typ": "access",
            "iat": now,
            "exp": now + ttl,
        }
        return jwt.encode(
            claims, self._keys[self._kid], algorithm="HS256", headers={"kid": self._kid}
        )

    def decode_access(self, token: str) -> dict[str, Any]:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            key = self._keys.get(kid) if isinstance(kid, str) else None
            if key is None:
                raise AuthenticationError("INVALID_TOKEN", "Invalid access token")
            claims: dict[str, Any] = jwt.decode(
                token, key, algorithms=["HS256"], options={"require": ["exp", "sub"]}
            )
        except jwt.PyJWTError as exc:
            raise AuthenticationError("INVALID_TOKEN", "Invalid access token") from exc
        if claims.get("typ") != "access":
            raise AuthenticationError("INVALID_TOKEN", "Invalid access token")
        return claims


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
