from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.api.schemas import CursorParams, ORMModel, StrictModel
from app.core.enums import ApprovalStatus


class ApprovalResponse(ORMModel):
    id: UUID
    player_profile_id: UUID
    player_display_name: str
    trainer_id: UUID
    business_name: str
    subject_type: Literal["event_rsvp", "content", "token_purchase"]
    subject_id: UUID
    amount_cents: int | None
    currency: Literal["usd"] = "usd"
    token_amount: int | None
    status: Literal["pending", "approved", "denied", "expired"]
    requested_at: datetime
    expires_at: datetime
    responded_at: datetime | None
    parent_note: str | None


class ApprovalListParams(CursorParams):
    status: ApprovalStatus = ApprovalStatus.PENDING


class ApprovalDecision(StrictModel):
    parent_note: str | None = Field(None, max_length=500)
