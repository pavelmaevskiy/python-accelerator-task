import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.models.core import IdempotencyKey


class IdempotencyRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self, principal_id: uuid.UUID, key: str, endpoint: str, request_hash: str
    ) -> bool:
        """Insert the key; ``False`` when it already exists.

        A concurrent in-flight claim of the same key blocks here until that transaction ends,
        so a ``False`` result always means the winner has committed.
        """
        stmt = (
            pg_insert(IdempotencyKey)
            .values(
                id=new_id(),
                key=key,
                principal_id=principal_id,
                endpoint=endpoint,
                request_hash=request_hash,
            )
            .on_conflict_do_nothing(index_elements=["principal_id", "key"])
            .returning(IdempotencyKey.id)
        )
        return (await self._session.execute(stmt)).first() is not None

    async def get(self, principal_id: uuid.UUID, key: str) -> IdempotencyKey | None:
        stmt = (
            select(IdempotencyKey)
            .where(IdempotencyKey.principal_id == principal_id, IdempotencyKey.key == key)
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def store_response(
        self, principal_id: uuid.UUID, key: str, status: int, body: dict[str, Any]
    ) -> None:
        await self._session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.principal_id == principal_id, IdempotencyKey.key == key)
            .values(response_status=status, response_body=body)
        )
