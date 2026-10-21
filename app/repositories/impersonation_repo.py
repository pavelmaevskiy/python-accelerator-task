import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import ImpersonationSession


class ImpersonationRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: ImpersonationSession) -> ImpersonationSession:
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_active(self, jti: str, *, now: datetime) -> ImpersonationSession | None:
        """A session is active until it is ended or passes ``expires_at``."""
        stmt = select(ImpersonationSession).where(
            ImpersonationSession.jti == jti,
            ImpersonationSession.ended_at.is_(None),
            ImpersonationSession.expires_at > now,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def end(self, jti: str, *, now: datetime) -> bool:
        result = await self._session.execute(
            update(ImpersonationSession)
            .where(ImpersonationSession.jti == jti, ImpersonationSession.ended_at.is_(None))
            .values(ended_at=now)
            .returning(ImpersonationSession.id)
        )
        return result.first() is not None

    async def end_all_for_user(self, user_id: uuid.UUID, *, now: datetime) -> None:
        """End every open session where the user is the target or the admin (erasure)."""
        await self._session.execute(
            update(ImpersonationSession)
            .where(
                ImpersonationSession.ended_at.is_(None),
                (ImpersonationSession.target_user_id == user_id)
                | (ImpersonationSession.admin_user_id == user_id),
            )
            .values(ended_at=now)
        )

    async def end_expired(self, *, now: datetime) -> int:
        """Set-based and idempotent: close open sessions past ``expires_at``."""
        result = await self._session.execute(
            update(ImpersonationSession)
            .where(ImpersonationSession.ended_at.is_(None), ImpersonationSession.expires_at <= now)
            .values(ended_at=ImpersonationSession.expires_at)
            .returning(ImpersonationSession.id)
        )
        return len(result.all())
