import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.approvals import ApprovalView
from app.core.enums import ApprovalStatus
from app.models.identity import PlayerProfile, PurchaseApproval, TrainerProfile


@dataclass(frozen=True, slots=True)
class ApprovalRow:
    approval: PurchaseApproval
    player_display_name: str
    business_name: str

    def view(self) -> ApprovalView:
        a = self.approval
        return ApprovalView(
            id=a.id,
            player_profile_id=a.player_profile_id,
            player_display_name=self.player_display_name,
            parent_user_id=a.parent_user_id,
            trainer_id=a.trainer_id,
            business_name=self.business_name,
            subject_type=a.subject_type,
            subject_id=a.subject_id,
            amount_cents=a.amount_cents,
            token_amount=a.token_amount,
            status=a.status,
            requested_at=a.requested_at,
            expires_at=a.expires_at,
            responded_at=a.responded_at,
            parent_note=a.parent_note,
        )


def _rows_stmt() -> Select[PurchaseApproval, str, str]:
    return (
        select(PurchaseApproval, PlayerProfile.display_name, TrainerProfile.business_name)
        .join(PlayerProfile, PlayerProfile.id == PurchaseApproval.player_profile_id)
        .join(TrainerProfile, TrainerProfile.user_id == PurchaseApproval.trainer_id)
    )


class ApprovalRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, approval: PurchaseApproval) -> PurchaseApproval:
        self._session.add(approval)
        await self._session.flush()
        return approval

    async def get_for_parent(
        self, approval_id: uuid.UUID, parent_user_id: uuid.UUID, *, for_update: bool = False
    ) -> ApprovalRow | None:
        stmt = _rows_stmt().where(
            PurchaseApproval.id == approval_id, PurchaseApproval.parent_user_id == parent_user_id
        )
        stmt = stmt.execution_options(populate_existing=True)
        if for_update:
            stmt = stmt.with_for_update(of=PurchaseApproval)
        row = (await self._session.execute(stmt)).one_or_none()
        return None if row is None else ApprovalRow(row[0], row[1], row[2])

    async def list_page(
        self,
        parent_user_id: uuid.UUID,
        status: ApprovalStatus,
        cursor: tuple[datetime, uuid.UUID] | None,
        limit: int,
    ) -> list[ApprovalRow]:
        stmt = _rows_stmt().where(
            PurchaseApproval.parent_user_id == parent_user_id, PurchaseApproval.status == status
        )
        if cursor is not None:
            stmt = stmt.where(
                tuple_(PurchaseApproval.requested_at, PurchaseApproval.id) < tuple_(*cursor)
            )
        stmt = stmt.order_by(PurchaseApproval.requested_at.desc(), PurchaseApproval.id.desc())
        rows = (await self._session.execute(stmt.limit(limit + 1))).all()
        return [ApprovalRow(r[0], r[1], r[2]) for r in rows]

    async def expire_due(self, *, now: datetime) -> list[uuid.UUID]:
        """Set-based and idempotent: pending rows past ``expires_at`` become ``expired``."""
        result = await self._session.execute(
            update(PurchaseApproval)
            .where(
                PurchaseApproval.status == ApprovalStatus.PENDING,
                PurchaseApproval.expires_at < now,
            )
            .values(status=ApprovalStatus.EXPIRED, responded_at=now)
            .returning(PurchaseApproval.id)
        )
        return [r[0] for r in result.all()]

    async def get_rows(self, ids: list[uuid.UUID]) -> list[ApprovalRow]:
        if not ids:
            return []
        rows = (await self._session.execute(_rows_stmt().where(PurchaseApproval.id.in_(ids)))).all()
        return [ApprovalRow(r[0], r[1], r[2]) for r in rows]
