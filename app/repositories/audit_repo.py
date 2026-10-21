from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AuditLog


class AuditRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, row: AuditLog) -> AuditLog:
        self._session.add(row)
        await self._session.flush()
        return row
