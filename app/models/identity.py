"""Identity, tenancy and auth tables for Epic-01 (see specs/architect-architecture.md C1-C5, C8).

Not modelled here (migration-only, see 0001_epic01_identity): the ``span`` generated column and
the two gist ``EXCLUDE`` constraints on ``availability_windows`` (C4), and the trigram indexes.
"""

import uuid
from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    ApprovalStatus,
    ApprovalSubjectType,
    Gender,
    LinkSource,
    LinkStatus,
    OneTimeTokenKind,
    ShareLinkEventKind,
    ShareLinkKind,
    SkillLevel,
    UserRole,
    UserStatus,
)
from app.core.ids import new_id
from app.db.base import Base, TimestampMixin, pg_enum


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=new_id)


def _fk(target: str, *, nullable: bool = False, index: bool = False) -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(target, ondelete="RESTRICT"), nullable=nullable, index=index
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(pg_enum(UserRole, "user_role"), nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"),
        nullable=False,
        default=UserStatus.PENDING,
        server_default=UserStatus.PENDING.value,
    )
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_users_role_status", "role", "status"),
        Index("ix_users_created_id", "created_at", "id"),
    )


class Profile(TimestampMixin, Base):
    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    avatar_key: Mapped[str | None] = mapped_column(String(255))
    school: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="UTC", server_default="UTC"
    )


class TrainerProfile(TimestampMixin, Base):
    __tablename__ = "trainer_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    website: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(2000))
    logo_key: Mapped[str | None] = mapped_column(String(255))
    brand_primary_color: Mapped[str | None] = mapped_column(CHAR(7))
    brand_secondary_color: Mapped[str | None] = mapped_column(CHAR(7))

    __table_args__ = (
        CheckConstraint(
            "brand_primary_color ~ '^#[0-9A-Fa-f]{6}$'", name="brand_primary_color_hex"
        ),
        CheckConstraint(
            "brand_secondary_color ~ '^#[0-9A-Fa-f]{6}$'", name="brand_secondary_color_hex"
        ),
    )


class CoachProfile(TimestampMixin, Base):
    __tablename__ = "coach_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    bio: Mapped[str | None] = mapped_column(Text)
    credentials: Mapped[str | None] = mapped_column(Text)
    certifications: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class CoachMembership(TimestampMixin, Base):
    __tablename__ = "coach_memberships"

    id: Mapped[uuid.UUID] = _uuid_pk()
    coach_user_id: Mapped[uuid.UUID] = _fk("users.id", index=True)
    trainer_id: Mapped[uuid.UUID] = _fk("trainer_profiles.user_id", index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "uq_coach_one_active_trainer",
            "coach_user_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class PlayerProfile(TimestampMixin, Base):
    __tablename__ = "player_profiles"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_user_id: Mapped[uuid.UUID] = _fk("users.id")
    login_user_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Nullable only for the implicit own profile of a self-registered adult (no birth date given).
    birth_date: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[Gender] = mapped_column(
        pg_enum(Gender, "gender"),
        nullable=False,
        default=Gender.UNSPECIFIED,
        server_default=Gender.UNSPECIFIED.value,
    )
    skill_level: Mapped[SkillLevel | None] = mapped_column(pg_enum(SkillLevel, "skill_level"))
    school: Mapped[str | None] = mapped_column(String(255))
    jersey_number: Mapped[int | None] = mapped_column(Integer)
    is_dependent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    emergency_contact_name: Mapped[str | None] = mapped_column(String(100))
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(20))
    token_spend_requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (
        CheckConstraint("birth_date > DATE '1900-01-01'", name="birth_date_after_1900"),
        CheckConstraint(
            "NOT is_dependent OR birth_date IS NOT NULL", name="dependent_has_birth_date"
        ),
        Index("ix_player_profiles_owner", "owner_user_id"),
        Index("ix_player_profiles_login", "login_user_id"),
        Index(
            "uq_player_profiles_one_own_per_owner",
            "owner_user_id",
            unique=True,
            postgresql_where=text("NOT is_dependent"),
        ),
    )


class TrainerPlayerLink(TimestampMixin, Base):
    """The tenancy join: a player belongs to a trainer iff an active link exists."""

    __tablename__ = "trainer_player_links"

    id: Mapped[uuid.UUID] = _uuid_pk()
    trainer_id: Mapped[uuid.UUID] = _fk("trainer_profiles.user_id")
    player_profile_id: Mapped[uuid.UUID] = _fk("player_profiles.id")
    source: Mapped[LinkSource] = mapped_column(pg_enum(LinkSource, "link_source"), nullable=False)
    share_link_id: Mapped[uuid.UUID | None] = _fk("share_links.id", nullable=True)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    status: Mapped[LinkStatus] = mapped_column(
        pg_enum(LinkStatus, "link_status"),
        nullable=False,
        default=LinkStatus.ACTIVE,
        server_default=LinkStatus.ACTIVE.value,
    )

    __table_args__ = (
        UniqueConstraint("trainer_id", "player_profile_id", name="uq_tpl_trainer_player"),
        Index("ix_tpl_trainer_status", "trainer_id", "status"),
        Index("ix_tpl_player", "player_profile_id", "status"),
    )


class ShareLink(TimestampMixin, Base):
    __tablename__ = "share_links"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    trainer_id: Mapped[uuid.UUID] = _fk("trainer_profiles.user_id", index=True)
    created_by_user_id: Mapped[uuid.UUID] = _fk("users.id")
    kind: Mapped[ShareLinkKind] = mapped_column(
        pg_enum(ShareLinkKind, "share_link_kind"), nullable=False
    )
    target_email: Mapped[str | None] = mapped_column(CITEXT)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_uses: Mapped[int | None] = mapped_column(Integer)
    use_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (
        CheckConstraint(
            "kind <> 'player_static' OR (expires_at IS NULL AND max_uses IS NULL)",
            name="static_link_unbounded",
        ),
        CheckConstraint(
            "kind <> 'coach_unique' OR (expires_at IS NOT NULL AND max_uses = 1)",
            name="unique_link_bounded",
        ),
    )


class ShareLinkEvent(Base):
    __tablename__ = "share_link_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    share_link_id: Mapped[uuid.UUID] = _fk("share_links.id", index=True)
    kind: Mapped[ShareLinkEventKind] = mapped_column(
        pg_enum(ShareLinkEventKind, "share_link_event_kind"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_agent_hash: Mapped[str | None] = mapped_column(CHAR(64))
    ip_hash: Mapped[str | None] = mapped_column(CHAR(64))


class AvailabilityWindow(TimestampMixin, Base):
    __tablename__ = "availability_windows"

    id: Mapped[uuid.UUID] = _uuid_pk()
    player_profile_id: Mapped[uuid.UUID | None] = _fk("player_profiles.id", nullable=True)
    coach_user_id: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True)
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("start_time < end_time", name="start_before_end"),
        CheckConstraint(
            "num_nonnulls(player_profile_id, coach_user_id) = 1", name="exactly_one_subject"
        ),
        Index("ix_availability_player", "player_profile_id", "weekday"),
        Index("ix_availability_coach", "coach_user_id", "weekday"),
    )


class PurchaseApproval(TimestampMixin, Base):
    __tablename__ = "purchase_approvals"

    id: Mapped[uuid.UUID] = _uuid_pk()
    player_profile_id: Mapped[uuid.UUID] = _fk("player_profiles.id")
    parent_user_id: Mapped[uuid.UUID] = _fk("users.id", index=True)
    trainer_id: Mapped[uuid.UUID] = _fk("trainer_profiles.user_id")
    subject_type: Mapped[ApprovalSubjectType] = mapped_column(
        pg_enum(ApprovalSubjectType, "approval_subject_type"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount_cents: Mapped[int | None] = mapped_column(Integer)
    token_amount: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ApprovalStatus] = mapped_column(
        pg_enum(ApprovalStatus, "approval_status"),
        nullable=False,
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
    )
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parent_note: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (Index("ix_purchase_approvals_pending", "status", "expires_at"),)


class ImpersonationSession(TimestampMixin, Base):
    __tablename__ = "impersonation_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    admin_user_id: Mapped[uuid.UUID] = _fk("users.id")
    target_user_id: Mapped[uuid.UUID] = _fk("users.id")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    jti: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("ix_impersonation_jti", "jti", unique=True),)


class UserErasure(Base):
    """GDPR ledger. ``original_user_id`` deliberately has no FK."""

    __tablename__ = "user_erasures"

    id: Mapped[uuid.UUID] = _uuid_pk()
    original_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    original_email_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    erased_by_user_id: Mapped[uuid.UUID] = _fk("users.id")
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    erased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    legal_retention_until: Mapped[date | None] = mapped_column(Date)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _fk("users.id", index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent_hash: Mapped[str | None] = mapped_column(CHAR(64))
    ip_hash: Mapped[str | None] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_refresh_family", "family_id", postgresql_where=text("revoked_at IS NULL")),
    )


class OneTimeToken(Base):
    __tablename__ = "one_time_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    kind: Mapped[OneTimeTokenKind] = mapped_column(
        pg_enum(OneTimeTokenKind, "one_time_token_kind"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_one_time_tokens_user_kind",
            "user_id",
            "kind",
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )
