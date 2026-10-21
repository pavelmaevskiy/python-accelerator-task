"""Epic-01 identity, tenancy and auth schema (hand-reviewed; autogenerate was only the draft).

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE TYPE timerange AS RANGE (subtype = time)")

    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_feature_flags")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("super_admin", "trainer", "coach", "player", name="user_role"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "active", "inactive", "deleted", name="user_status"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "is_email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index("ix_users_created_id", "users", ["created_at", "id"], unique=False)
    op.create_index("ix_users_role_status", "users", ["role", "status"], unique=False)
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("impersonator_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.UUID(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_log_actor_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["impersonator_id"],
            ["users.id"],
            name=op.f("fk_audit_log_impersonator_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        "ix_audit_log_action_created", "audit_log", ["action", "created_at"], unique=False
    )
    op.create_index("ix_audit_log_actor", "audit_log", ["actor_user_id"], unique=False)
    op.create_table(
        "coach_profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("credentials", sa.Text(), nullable=True),
        sa.Column(
            "certifications",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_public", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_coach_profiles_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_coach_profiles")),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("principal_id", sa.UUID(), nullable=False),
        sa.Column("endpoint", sa.String(length=200), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["users.id"],
            name=op.f("fk_idempotency_keys_principal_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("principal_id", "key", name="uq_idempotency_principal_key"),
    )
    op.create_table(
        "impersonation_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("admin_user_id", sa.UUID(), nullable=False),
        sa.Column("target_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["users.id"],
            name=op.f("fk_impersonation_sessions_admin_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            name=op.f("fk_impersonation_sessions_target_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_impersonation_sessions")),
    )
    op.create_index("ix_impersonation_jti", "impersonation_sessions", ["jti"], unique=True)
    op.create_table(
        "one_time_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "email_verify",
                "password_reset",
                "trainer_setup",
                "email_change",
                name="one_time_token_kind",
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_one_time_tokens_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_one_time_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_one_time_tokens_token_hash")),
    )
    op.create_index(
        "ix_one_time_tokens_user_kind",
        "one_time_tokens",
        ["user_id", "kind"],
        unique=False,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    op.create_table(
        "player_profiles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_user_id", sa.UUID(), nullable=False),
        sa.Column("login_user_id", sa.UUID(), nullable=True),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=False),
        sa.Column("gender", sa.Enum("male", "female", "other", name="gender"), nullable=False),
        sa.Column(
            "skill_level",
            sa.Enum("beginner", "intermediate", "advanced", name="skill_level"),
            nullable=True,
        ),
        sa.Column("school", sa.String(length=255), nullable=True),
        sa.Column("jersey_number", sa.Integer(), nullable=True),
        sa.Column("is_dependent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("emergency_contact_name", sa.String(length=100), nullable=True),
        sa.Column("emergency_contact_phone", sa.String(length=20), nullable=True),
        sa.Column(
            "token_spend_requires_approval",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "birth_date > DATE '1900-01-01'", name=op.f("ck_player_profiles_birth_date_after_1900")
        ),
        sa.ForeignKeyConstraint(
            ["login_user_id"],
            ["users.id"],
            name=op.f("fk_player_profiles_login_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_player_profiles_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_player_profiles")),
    )
    op.create_index("ix_player_profiles_login", "player_profiles", ["login_user_id"], unique=False)
    op.create_index("ix_player_profiles_owner", "player_profiles", ["owner_user_id"], unique=False)
    op.create_index(
        "uq_player_profiles_one_own_per_owner",
        "player_profiles",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text("NOT is_dependent"),
    )
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("first_name", sa.String(length=100), nullable=False),
        sa.Column("last_name", sa.String(length=100), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("avatar_key", sa.String(length=255), nullable=True),
        sa.Column("school", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_profiles_user_id_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_profiles")),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("ip_hash", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index(
        "ix_refresh_family",
        "refresh_tokens",
        ["family_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_table(
        "trainer_profiles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("business_name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("logo_key", sa.String(length=255), nullable=True),
        sa.Column("brand_primary_color", sa.CHAR(length=7), nullable=True),
        sa.Column("brand_secondary_color", sa.CHAR(length=7), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "brand_primary_color ~ '^#[0-9A-Fa-f]{6}$'",
            name=op.f("ck_trainer_profiles_brand_primary_color_hex"),
        ),
        sa.CheckConstraint(
            "brand_secondary_color ~ '^#[0-9A-Fa-f]{6}$'",
            name=op.f("ck_trainer_profiles_brand_secondary_color_hex"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_trainer_profiles_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_trainer_profiles")),
        sa.UniqueConstraint("slug", name=op.f("uq_trainer_profiles_slug")),
    )
    op.create_table(
        "user_erasures",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("original_user_id", sa.UUID(), nullable=False),
        sa.Column("original_email_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("erased_by_user_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "erased_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("legal_retention_until", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["erased_by_user_id"],
            ["users.id"],
            name=op.f("fk_user_erasures_erased_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_erasures")),
    )
    op.create_table(
        "availability_windows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("player_profile_id", sa.UUID(), nullable=True),
        sa.Column("coach_user_id", sa.UUID(), nullable=True),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "num_nonnulls(player_profile_id, coach_user_id) = 1",
            name=op.f("ck_availability_windows_exactly_one_subject"),
        ),
        sa.CheckConstraint(
            "start_time < end_time", name=op.f("ck_availability_windows_start_before_end")
        ),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6", name=op.f("ck_availability_windows_weekday_range")
        ),
        sa.ForeignKeyConstraint(
            ["coach_user_id"],
            ["users.id"],
            name=op.f("fk_availability_windows_coach_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["player_profile_id"],
            ["player_profiles.id"],
            name=op.f("fk_availability_windows_player_profile_id_player_profiles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_availability_windows")),
    )
    op.create_index(
        "ix_availability_coach", "availability_windows", ["coach_user_id", "weekday"], unique=False
    )
    op.create_index(
        "ix_availability_player",
        "availability_windows",
        ["player_profile_id", "weekday"],
        unique=False,
    )
    # C4: half-open span + one exclusion constraint per subject FK (16:00-18:00 and 18:00-20:00 coexist)
    op.execute(
        "ALTER TABLE availability_windows ADD COLUMN span timerange "
        "GENERATED ALWAYS AS (timerange(start_time, end_time, '[)')) STORED"
    )
    op.execute(
        "ALTER TABLE availability_windows ADD CONSTRAINT ex_availability_player_overlap "
        "EXCLUDE USING gist (player_profile_id WITH =, weekday WITH =, span WITH &&) "
        "WHERE (player_profile_id IS NOT NULL)"
    )
    op.execute(
        "ALTER TABLE availability_windows ADD CONSTRAINT ex_availability_coach_overlap "
        "EXCLUDE USING gist (coach_user_id WITH =, weekday WITH =, span WITH &&) "
        "WHERE (coach_user_id IS NOT NULL)"
    )
    op.create_table(
        "coach_memberships",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("coach_user_id", sa.UUID(), nullable=False),
        sa.Column("trainer_id", sa.UUID(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["coach_user_id"],
            ["users.id"],
            name=op.f("fk_coach_memberships_coach_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trainer_id"],
            ["trainer_profiles.user_id"],
            name=op.f("fk_coach_memberships_trainer_id_trainer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coach_memberships")),
    )
    op.create_index(
        op.f("ix_coach_memberships_coach_user_id"),
        "coach_memberships",
        ["coach_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_coach_memberships_trainer_id"), "coach_memberships", ["trainer_id"], unique=False
    )
    op.create_index(
        "uq_coach_one_active_trainer",
        "coach_memberships",
        ["coach_user_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "purchase_approvals",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("player_profile_id", sa.UUID(), nullable=False),
        sa.Column("parent_user_id", sa.UUID(), nullable=False),
        sa.Column("trainer_id", sa.UUID(), nullable=False),
        sa.Column(
            "subject_type",
            sa.Enum("event_rsvp", "content", "token_purchase", name="approval_subject_type"),
            nullable=False,
        ),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("token_amount", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "denied", "expired", name="approval_status"),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parent_note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["parent_user_id"],
            ["users.id"],
            name=op.f("fk_purchase_approvals_parent_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["player_profile_id"],
            ["player_profiles.id"],
            name=op.f("fk_purchase_approvals_player_profile_id_player_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trainer_id"],
            ["trainer_profiles.user_id"],
            name=op.f("fk_purchase_approvals_trainer_id_trainer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_approvals")),
    )
    op.create_index(
        op.f("ix_purchase_approvals_parent_user_id"),
        "purchase_approvals",
        ["parent_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_purchase_approvals_pending",
        "purchase_approvals",
        ["status", "expires_at"],
        unique=False,
    )
    op.create_table(
        "share_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("trainer_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind", sa.Enum("player_static", "coach_unique", name="share_link_kind"), nullable=False
        ),
        sa.Column("target_email", postgresql.CITEXT(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind <> 'coach_unique' OR (expires_at IS NOT NULL AND max_uses = 1)",
            name=op.f("ck_share_links_unique_link_bounded"),
        ),
        sa.CheckConstraint(
            "kind <> 'player_static' OR (expires_at IS NULL AND max_uses IS NULL)",
            name=op.f("ck_share_links_static_link_unbounded"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_share_links_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trainer_id"],
            ["trainer_profiles.user_id"],
            name=op.f("fk_share_links_trainer_id_trainer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_share_links")),
        sa.UniqueConstraint("code", name=op.f("uq_share_links_code")),
    )
    op.create_index(op.f("ix_share_links_trainer_id"), "share_links", ["trainer_id"], unique=False)
    op.create_table(
        "share_link_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("share_link_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind", sa.Enum("opened", "registered", name="share_link_event_kind"), nullable=False
        ),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_agent_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("ip_hash", sa.CHAR(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["share_link_id"],
            ["share_links.id"],
            name=op.f("fk_share_link_events_share_link_id_share_links"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_share_link_events")),
    )
    op.create_index(
        op.f("ix_share_link_events_share_link_id"),
        "share_link_events",
        ["share_link_id"],
        unique=False,
    )
    op.create_table(
        "trainer_player_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("trainer_id", sa.UUID(), nullable=False),
        sa.Column("player_profile_id", sa.UUID(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "share_link",
                "event_registration",
                "coach_invite",
                "camp_conversion",
                "admin",
                name="link_source",
            ),
            nullable=False,
        ),
        sa.Column("share_link_id", sa.UUID(), nullable=True),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum("active", "inactive", name="link_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["player_profile_id"],
            ["player_profiles.id"],
            name=op.f("fk_trainer_player_links_player_profile_id_player_profiles"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["share_link_id"],
            ["share_links.id"],
            name=op.f("fk_trainer_player_links_share_link_id_share_links"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["trainer_id"],
            ["trainer_profiles.user_id"],
            name=op.f("fk_trainer_player_links_trainer_id_trainer_profiles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trainer_player_links")),
        sa.UniqueConstraint("trainer_id", "player_profile_id", name="uq_tpl_trainer_player"),
    )
    op.create_index(
        "ix_tpl_player", "trainer_player_links", ["player_profile_id", "status"], unique=False
    )
    op.create_index(
        "ix_tpl_trainer_status", "trainer_player_links", ["trainer_id", "status"], unique=False
    )
    op.execute("CREATE INDEX ix_users_email_trgm ON users USING gin ((email::text) gin_trgm_ops)")
    op.execute(
        "CREATE INDEX ix_profiles_first_name_trgm ON profiles USING gin (first_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_profiles_last_name_trgm ON profiles USING gin (last_name gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_profiles_last_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_profiles_first_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_users_email_trgm")
    op.drop_index("ix_tpl_trainer_status", table_name="trainer_player_links")
    op.drop_index("ix_tpl_player", table_name="trainer_player_links")
    op.drop_table("trainer_player_links")
    op.drop_index(op.f("ix_share_link_events_share_link_id"), table_name="share_link_events")
    op.drop_table("share_link_events")
    op.drop_index(op.f("ix_share_links_trainer_id"), table_name="share_links")
    op.drop_table("share_links")
    op.drop_index("ix_purchase_approvals_pending", table_name="purchase_approvals")
    op.drop_index(op.f("ix_purchase_approvals_parent_user_id"), table_name="purchase_approvals")
    op.drop_table("purchase_approvals")
    op.drop_index(
        "uq_coach_one_active_trainer",
        table_name="coach_memberships",
        postgresql_where=sa.text("is_active"),
    )
    op.drop_index(op.f("ix_coach_memberships_trainer_id"), table_name="coach_memberships")
    op.drop_index(op.f("ix_coach_memberships_coach_user_id"), table_name="coach_memberships")
    op.drop_table("coach_memberships")
    op.drop_index("ix_availability_player", table_name="availability_windows")
    op.drop_index("ix_availability_coach", table_name="availability_windows")
    op.drop_table("availability_windows")  # drops span column and exclusion constraints with it
    op.drop_table("user_erasures")
    op.drop_table("trainer_profiles")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(
        "ix_refresh_family",
        table_name="refresh_tokens",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("refresh_tokens")
    op.drop_table("profiles")
    op.drop_index(
        "uq_player_profiles_one_own_per_owner",
        table_name="player_profiles",
        postgresql_where=sa.text("NOT is_dependent"),
    )
    op.drop_index("ix_player_profiles_owner", table_name="player_profiles")
    op.drop_index("ix_player_profiles_login", table_name="player_profiles")
    op.drop_table("player_profiles")
    op.drop_index(
        "ix_one_time_tokens_user_kind",
        table_name="one_time_tokens",
        postgresql_where=sa.text("consumed_at IS NULL"),
    )
    op.drop_table("one_time_tokens")
    op.drop_index("ix_impersonation_jti", table_name="impersonation_sessions")
    op.drop_table("impersonation_sessions")
    op.drop_table("idempotency_keys")
    op.drop_table("coach_profiles")
    op.drop_index("ix_audit_log_actor", table_name="audit_log")
    op.drop_index("ix_audit_log_action_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_users_role_status", table_name="users")
    op.drop_index("ix_users_created_id", table_name="users")
    op.drop_table("users")
    op.drop_table("feature_flags")
    op.execute("DROP TYPE timerange")
    for enum_name in (
        "user_role",
        "user_status",
        "gender",
        "skill_level",
        "link_source",
        "link_status",
        "share_link_kind",
        "share_link_event_kind",
        "approval_subject_type",
        "approval_status",
        "one_time_token_kind",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
