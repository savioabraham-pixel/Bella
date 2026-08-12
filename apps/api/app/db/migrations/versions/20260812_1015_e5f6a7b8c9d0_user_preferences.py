"""Add user_preferences

Theme, voice, notification and retention settings — the server-side home for the `b_dark`,
`b_clock_fmt`, `b_location_perm` and `b_pref_*` keys the legacy client kept in localStorage,
where they were lost on every device change and unreadable to the backend that needed them.

A side table rather than columns on `profiles`, mirroring `user_quotas`, and RLS-scoped like
every other user-owned table — the security suite asserts that membership.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Created: 2026-08-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "bella_app"


def upgrade() -> None:
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "theme", sa.String(length=16), server_default=sa.text("'system'"), nullable=False
        ),
        sa.Column("clock_24h", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("voice_replies", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("voice_autoplay", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("hands_free", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "notifications_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "location_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("message_retention_days", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "theme IN ('light', 'dark', 'system')",
            name=op.f("ck_user_preferences_theme_valid"),
        ),
        sa.CheckConstraint(
            "message_retention_days IS NULL OR message_retention_days IN (30, 90, 365)",
            name=op.f("ck_user_preferences_retention_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_preferences_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_preferences")),
    )

    op.execute("ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_preferences NO FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY user_preferences_owner ON user_preferences
            FOR ALL
            TO {APP_ROLE}
            USING (user_id = current_setting('app.user_id', true)::uuid)
            WITH CHECK (user_id = current_setting('app.user_id', true)::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS user_preferences_owner ON user_preferences")
    op.drop_table("user_preferences")
