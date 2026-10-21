"""Resolve a verified access-token claim set into a ``Principal`` (one lightweight read set)."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import Clock
from app.core.enums import UserRole, UserStatus
from app.core.errors import AuthenticationError, NotFoundError
from app.core.principal import Principal
from app.repositories.impersonation_repo import ImpersonationRepo
from app.repositories.tenancy_repo import TenancyRepo
from app.repositories.user_repo import UserRepo


def _parse_uuid(value: object, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise AuthenticationError("INVALID_TOKEN", f"Invalid {what} claim") from exc


class PrincipalService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def resolve(self, claims: dict[str, Any], trainer_context: uuid.UUID | None) -> Principal:
        """User state is re-read on every request so deactivation takes effect immediately."""
        user_id = _parse_uuid(claims["sub"], "sub")
        act_claim = claims.get("act")
        jti = claims.get("jti")
        async with self._sessionmaker() as session:
            state = await UserRepo(session).get_auth_state(user_id)
            if state is None or state.status != UserStatus.ACTIVE:
                raise AuthenticationError("ACCOUNT_INACTIVE", "Account is not active")
            act: uuid.UUID | None = None
            if act_claim:
                active = await ImpersonationRepo(session).get_active(
                    str(jti), now=self._clock.now()
                )
                if active is None:
                    raise AuthenticationError(
                        "IMPERSONATION_EXPIRED", "Impersonation session ended or expired"
                    )
                act = _parse_uuid(act_claim, "act")
            tenancy = TenancyRepo(session)
            trainer_id: uuid.UUID | None = None
            if state.role == UserRole.TRAINER:
                trainer_id = user_id
            elif state.role == UserRole.COACH:
                trainer_id = await tenancy.get_active_trainer_of_coach(user_id)
            elif state.role == UserRole.PLAYER and trainer_context is not None:
                if not await tenancy.user_has_active_link_to(user_id, trainer_context):
                    raise NotFoundError("NOT_FOUND", "Trainer context not found")
                trainer_id = trainer_context
            is_child = (
                await tenancy.is_child_login(user_id) if state.role == UserRole.PLAYER else False
            )
        return Principal(
            user_id=user_id,
            role=state.role,
            trainer_id=trainer_id,
            act=act,
            is_child=is_child,
            jti=str(jti) if jti else None,
        )
