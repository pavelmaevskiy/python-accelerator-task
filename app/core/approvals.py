"""Seam between the approval aggregate (Epic-01) and the epics that need approvals (02, 05).

The consumer side (``ApprovalSubjectHandler``) is declared here; Epic-02 (``event_rsvp``) and
Epic-05 (``content``, ``token_purchase``) register implementations at startup. Epic-01 ships an
empty registry.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ApprovalStatus, ApprovalSubjectType


@dataclass(frozen=True, slots=True)
class ApprovalView:
    id: uuid.UUID
    player_profile_id: uuid.UUID
    player_display_name: str
    parent_user_id: uuid.UUID
    trainer_id: uuid.UUID
    business_name: str
    subject_type: ApprovalSubjectType
    subject_id: uuid.UUID
    amount_cents: int | None
    token_amount: int | None
    status: ApprovalStatus
    requested_at: datetime
    expires_at: datetime
    responded_at: datetime | None
    parent_note: str | None
    currency: str = "usd"

    def to_json(self) -> dict[str, object]:
        """JSON-safe form, stored for idempotent replay."""
        return {
            "id": str(self.id),
            "player_profile_id": str(self.player_profile_id),
            "player_display_name": self.player_display_name,
            "parent_user_id": str(self.parent_user_id),
            "trainer_id": str(self.trainer_id),
            "business_name": self.business_name,
            "subject_type": self.subject_type.value,
            "subject_id": str(self.subject_id),
            "amount_cents": self.amount_cents,
            "token_amount": self.token_amount,
            "status": self.status.value,
            "requested_at": self.requested_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "responded_at": self.responded_at.isoformat() if self.responded_at else None,
            "parent_note": self.parent_note,
            "currency": self.currency,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> "ApprovalView":
        def opt_dt(key: str) -> datetime | None:
            raw = data[key]
            return None if raw is None else datetime.fromisoformat(str(raw))

        def opt_int(key: str) -> int | None:
            raw = data[key]
            return None if raw is None else int(str(raw))

        responded = opt_dt("responded_at")
        return cls(
            id=uuid.UUID(str(data["id"])),
            player_profile_id=uuid.UUID(str(data["player_profile_id"])),
            player_display_name=str(data["player_display_name"]),
            parent_user_id=uuid.UUID(str(data["parent_user_id"])),
            trainer_id=uuid.UUID(str(data["trainer_id"])),
            business_name=str(data["business_name"]),
            subject_type=ApprovalSubjectType(str(data["subject_type"])),
            subject_id=uuid.UUID(str(data["subject_id"])),
            amount_cents=opt_int("amount_cents"),
            token_amount=opt_int("token_amount"),
            status=ApprovalStatus(str(data["status"])),
            requested_at=datetime.fromisoformat(str(data["requested_at"])),
            expires_at=datetime.fromisoformat(str(data["expires_at"])),
            responded_at=responded,
            parent_note=None if data["parent_note"] is None else str(data["parent_note"]),
            currency=str(data["currency"]),
        )


class ApprovalSubjectHandler(Protocol):
    subject_type: ApprovalSubjectType

    async def on_approved(self, session: AsyncSession, approval: ApprovalView) -> None: ...

    async def on_denied_or_expired(self, session: AsyncSession, approval: ApprovalView) -> None: ...


class ApprovalHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[ApprovalSubjectType, ApprovalSubjectHandler] = {}

    def register(self, handler: ApprovalSubjectHandler) -> None:
        self._handlers[handler.subject_type] = handler

    def get(self, subject_type: ApprovalSubjectType) -> ApprovalSubjectHandler | None:
        return self._handlers.get(subject_type)
