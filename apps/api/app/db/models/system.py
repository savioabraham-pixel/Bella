"""Prompts, tools, flags, consent and audit.

`prompt_versions` is the important one: Bella's persona is the product, so it lives in the
database as versioned, cohorted, rollback-able data with an author — not as a string literal
inside a 315 KB file where changing it is an unreviewable diff.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin

CONSENT_KINDS = (
    "terms",
    "privacy",
    "memory_storage",
    "voice_recording",
    "location",
    "analytics",
    "marketing",
)
DATA_REQUEST_KINDS = ("export", "deletion", "rectification")
DATA_REQUEST_STATUSES = ("pending", "processing", "completed", "rejected", "cancelled")


class PromptVersion(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "prompt_versions"

    name: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "bella.system"
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)  # Jinja2
    variables: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    cohort: Mapped[str | None] = mapped_column(String(100))  # NULL = everyone
    notes: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_prompt_versions_name_version"),
        Index(
            "ix_prompt_versions_active",
            "name",
            "cohort",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )


class ToolInvocation(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "tool_invocations"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (Index("ix_tool_invocations_message", "message_id"),)


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    rollout_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    user_allowlist: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=text("'{}'")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (CheckConstraint("rollout_pct BETWEEN 0 AND 100", name="rollout_pct_range"),)


class Consent(Base, UUIDPrimaryKeyMixin):
    """Consent ledger.

    The legacy app collected health and bereavement details with no record of consent at
    all. Every grant and revocation is now an append-only row with the terms version it
    applied to.
    """

    __tablename__ = "consents"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # For minors: which guardian granted it.
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    granted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    ip_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        CheckConstraint(f"kind IN {CONSENT_KINDS}", name="kind_valid"),
        Index("ix_consents_user_kind", "user_id", "kind", text("granted_at DESC")),
    )


class AuditLog(Base):
    """Append-only. The application role is granted INSERT and SELECT, never UPDATE or
    DELETE — see the migration."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_role: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(50))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    audit_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    ip_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_audit_log_target_user", "target_user_id", text("created_at DESC")),
        Index("ix_audit_log_action", "action", text("created_at DESC")),
        Index("ix_audit_log_actor", "actor_user_id", text("created_at DESC")),
    )


class DataRequest(Base, UUIDPrimaryKeyMixin):
    """Export / erasure / rectification with an SLA clock the user can see.

    Replaces the legacy flow, which wrote a spreadsheet row and emailed the owner.
    """

    __tablename__ = "data_requests"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    reason: Mapped[str | None] = mapped_column(Text)

    requested_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    due_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    artifact_key: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        CheckConstraint(f"kind IN {DATA_REQUEST_KINDS}", name="kind_valid"),
        CheckConstraint(f"status IN {DATA_REQUEST_STATUSES}", name="status_valid"),
        Index(
            "ix_data_requests_open",
            "status",
            "due_at",
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
    )
