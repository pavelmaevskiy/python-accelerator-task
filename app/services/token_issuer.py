"""Issue access JWT + rotating opaque refresh token (shared by login, refresh, registration)."""

import uuid
from datetime import timedelta

from app.core.clock import Clock
from app.core.config import Settings
from app.core.enums import UserRole
from app.core.security import TokenCodec, generate_opaque_token, hash_token
from app.repositories.tenancy_repo import TenancyRepo
from app.repositories.token_repo import TokenRepo
from app.services.views import IssuedTokens


class TokenIssuer:
    def __init__(
        self,
        codec: TokenCodec,
        tokens: TokenRepo,
        tenancy: TenancyRepo,
        clock: Clock,
        settings: Settings,
    ) -> None:
        self._codec = codec
        self._tokens = tokens
        self._tenancy = tenancy
        self._clock = clock
        self._settings = settings

    async def issue_pair(
        self,
        user_id: uuid.UUID,
        role: UserRole,
        *,
        family_id: uuid.UUID | None,
        ip_hash: str | None,
        ua_hash: str | None,
    ) -> IssuedTokens:
        """Must run inside the caller's transaction (inserts the refresh row)."""
        refresh = generate_opaque_token()
        await self._tokens.create_refresh(
            user_id,
            family_id=family_id,
            token_hash=hash_token(refresh),
            expires_at=self._clock.now() + timedelta(days=self._settings.refresh_ttl_days),
            ip_hash=ip_hash,
            user_agent_hash=ua_hash,
        )
        return IssuedTokens(
            access_token=await self.issue_access(user_id, role),
            refresh_token=refresh,
            expires_in=self._settings.access_ttl_seconds,
        )

    async def issue_access(self, user_id: uuid.UUID, role: UserRole) -> str:
        trainer_id: uuid.UUID | None = None
        if role == UserRole.TRAINER:
            trainer_id = user_id
        elif role == UserRole.COACH:
            trainer_id = await self._tenancy.get_active_trainer_of_coach(user_id)
        return self._codec.encode_access(
            sub=str(user_id),
            role=role.value,
            trainer_id=str(trainer_id) if trainer_id else None,
            ttl=timedelta(seconds=self._settings.access_ttl_seconds),
        )
