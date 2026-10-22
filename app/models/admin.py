import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin


class AuditLog(Base):
    """Append-only. ``details`` must never contain PII (emails, names, tokens)."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    impersonator_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_audit_log_action_created", "action", "created_at"),
        Index("ix_audit_log_actor", "actor_user_id"),
    )


class FeatureFlag(TimestampMixin, Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    description: Mapped[str | None] = mapped_column(Text)
