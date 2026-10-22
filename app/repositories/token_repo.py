"""Refresh and one-time token persistence. Never commits; callers own the transaction."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import OneTimeTokenKind
from app.core.ids import new_id
from app.models.identity import OneTimeToken, RefreshToken


class TokenRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- one-time tokens ------------------------------------------------------------------

    async def issue_one_time(
        self,
        user_id: uuid.UUID,
        kind: OneTimeTokenKind | str,
        token_hash: str,
        expires_at: datetime,
        *,
        payload: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> OneTimeToken:
        """Invalidate earlier unconsumed tokens of the same (user, kind), then insert."""
        kind = OneTimeTokenKind(kind)
        consumed_at = now if now is not None else func.now()
        await self._session.execute(
            update(OneTimeToken)
            .where(
                OneTimeToken.user_id == user_id,
                OneTimeToken.kind == kind,
                OneTimeToken.consumed_at.is_(None),
            )
            .values(consumed_at=consumed_at)
        )
        row = OneTimeToken(
            user_id=user_id,
            kind=kind,
            token_hash=token_hash,
            payload=payload,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def consume_one_time(
        self,
        token_hash: str,
        *,
        now: datetime,
        kinds: Sequence[OneTimeTokenKind] | None = None,
    ) -> OneTimeToken | None:
        """Atomic single-use consumption: exactly one concurrent caller gets the row."""
        stmt = (
            update(OneTimeToken)
            .where(
                OneTimeToken.token_hash == token_hash,
                OneTimeToken.consumed_at.is_(None),
                OneTimeToken.expires_at > now,
            )
            .values(consumed_at=now)
            .returning(OneTimeToken)
            .execution_options(synchronize_session=False)
        )
        if kinds is not None:
            stmt = stmt.where(OneTimeToken.kind.in_(list(kinds)))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def consume_all_one_time_for_user(self, user_id: uuid.UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(OneTimeToken)
            .where(OneTimeToken.user_id == user_id, OneTimeToken.consumed_at.is_(None))
            .values(consumed_at=now)
        )

    # --- refresh tokens -------------------------------------------------------------------

    async def create_refresh(
        self,
        user_id: uuid.UUID,
        *,
        family_id: uuid.UUID | None,
        token_hash: str,
        expires_at: datetime,
        ip_hash: str | None = None,
        user_agent_hash: str | None = None,
    ) -> RefreshToken:
        row = RefreshToken(
            user_id=user_id,
            family_id=family_id or new_id(),
            token_hash=token_hash,
            expires_at=expires_at,
            ip_hash=ip_hash,
            user_agent_hash=user_agent_hash,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_refresh_by_hash(
        self, token_hash: str, *, for_update: bool = False
    ) -> RefreshToken | None:
        stmt = (
            select(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def mark_consumed(self, token: RefreshToken, *, now: datetime) -> None:
        token.consumed_at = now
        await self._session.flush()

    async def revoke_family(self, family_id: uuid.UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )

    async def revoke_all_for_user(self, user_id: uuid.UUID, *, now: datetime) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
            .execution_options(synchronize_session=False)
        )
