"""Audit trail. ``details`` must never contain PII (emails, names, raw tokens)."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.admin import AuditLog
from app.repositories.audit_repo import AuditRepo


class AuditService:
    def __init__(
        self, sessionmaker: async_sessionmaker[AsyncSession], repo: AuditRepo | None = None
    ) -> None:
        self._sessionmaker = sessionmaker
        self._repo = repo  # bound to the caller's session; only needed for ``record``

    @staticmethod
    def _row(
        actor_user_id: uuid.UUID | None,
        action: str,
        target_type: str | None,
        target_id: uuid.UUID | None,
        details: dict[str, Any] | None,
        impersonator_id: uuid.UUID | None,
    ) -> AuditLog:
        return AuditLog(
            actor_user_id=actor_user_id,
            impersonator_id=impersonator_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )

    async def record(
        self,
        *,
        actor_user_id: uuid.UUID | None,
        action: str,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
        impersonator_id: uuid.UUID | None = None,
    ) -> None:
        """Write inside the caller's transaction (rolls back with it)."""
        if self._repo is None:
            raise RuntimeError("AuditService.record needs a repo bound to the caller's session")
        row = self._row(actor_user_id, action, target_type, target_id, details, impersonator_id)
        await self._repo.add(row)

    async def record_isolated(
        self,
        *,
        actor_user_id: uuid.UUID | None,
        action: str,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
        impersonator_id: uuid.UUID | None = None,
    ) -> None:
        """Write in a separate short transaction so the row survives a failed request."""
        row = self._row(actor_user_id, action, target_type, target_id, details, impersonator_id)
        async with self._sessionmaker() as session, session.begin():
            await AuditRepo(session).add(row)
