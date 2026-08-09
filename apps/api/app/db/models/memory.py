"""Long-term memory.

The legacy design appended a line per fact and concatenated every line into the system
prompt. That grows without bound, dilutes attention, and cannot express "this fact replaced
that one" or "this hasn't come up in a year".

Here a memory is a scored, embedded, expiring row with provenance, so retrieval can pick
the eight that matter and the user can see and correct all of them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.identity import User

EMBEDDING_DIMENSIONS = 1536

MEMORY_KINDS = ("fact", "preference", "pronunciation", "followup", "event")
MEMORY_SOURCES = ("user_explicit", "extracted", "imported", "admin")
SENSITIVITY_LEVELS = ("normal", "high")


class Memory(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "memories"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True)
    )

    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Pinned per row: changing embedding models requires a re-index, and without this
    # column there is no way to tell which rows are stale.
    embedding_model: Mapped[str | None] = mapped_column(String(100))

    source: Mapped[str] = mapped_column(String(20), nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )

    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default=text("0.5")
    )
    salience: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default=text("0.5")
    )

    # Health, bereavement and finances. Never surfaced proactively, never in a digest.
    sensitivity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal", server_default=text("'normal'")
    )

    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    times_referenced: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_referenced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # Contradiction handling: both rows are kept so the history stays auditable.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("memories.id", ondelete="SET NULL")
    )

    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="memories")

    __table_args__ = (
        CheckConstraint(f"kind IN {MEMORY_KINDS}", name="kind_valid"),
        CheckConstraint(f"source IN {MEMORY_SOURCES}", name="source_valid"),
        CheckConstraint(f"sensitivity IN {SENSITIVITY_LEVELS}", name="sensitivity_valid"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        CheckConstraint("salience BETWEEN 0 AND 1", name="salience_range"),
        Index(
            "ix_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_where=text("deleted_at IS NULL AND superseded_by IS NULL"),
        ),
        Index("ix_memories_content_tsv", "content_tsv", postgresql_using="gin"),
        Index(
            "ix_memories_user_kind",
            "user_id",
            "kind",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_memories_expiry",
            "expires_at",
            postgresql_where=text("expires_at IS NOT NULL AND deleted_at IS NULL"),
        ),
    )
