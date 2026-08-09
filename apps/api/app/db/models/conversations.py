"""Threads and messages.

The legacy app kept conversations in `localStorage`, capped at the last 20 messages, and
wiped them on `pagehide`. Here they are durable, paginated and searchable, and a message
row exists *before* the model is called so a mid-generation crash leaves a retryable
record instead of nothing.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.identity import User

MESSAGE_ROLES = ("user", "assistant", "system", "tool")
MESSAGE_STATUSES = ("pending", "streaming", "complete", "failed", "cancelled")


class Thread(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "threads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        default="New Conversation",
        server_default=text("'New Conversation'"),
    )
    title_generated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Rolling summary of turns older than the verbatim window, so context stays bounded.
    summary: Mapped[str | None] = mapped_column(Text)
    summary_upto_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_message_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    pinned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    archived_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    user: Mapped[User] = relationship(back_populates="threads")
    messages: Mapped[list[Message]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="Message.seq",
    )

    __table_args__ = (
        Index(
            "ix_threads_user_recent",
            "user_id",
            text("last_message_at DESC"),
            postgresql_where=text("archived_at IS NULL"),
        ),
    )


class Message(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "messages"

    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised so RLS can filter without a join on every read.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True)
    )

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="complete", server_default=text("'complete'")
    )

    # Client-generated idempotency key: a double-tap on Send cannot bill twice.
    client_message_id: Mapped[str | None] = mapped_column(String(64))

    # Provider bookkeeping — what answered, how much it cost, how long it took.
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("prompt_versions.id", ondelete="SET NULL")
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_micros: Mapped[int | None] = mapped_column(BigInteger)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    first_token_ms: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    tool_calls: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    language: Mapped[str | None] = mapped_column(String(8))

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    thread: Mapped[Thread] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_messages_thread_id_seq"),
        UniqueConstraint(
            "user_id", "client_message_id", name="uq_messages_user_id_client_message_id"
        ),
        CheckConstraint(f"role IN {MESSAGE_ROLES}", name="role_valid"),
        CheckConstraint(f"status IN {MESSAGE_STATUSES}", name="status_valid"),
        Index("ix_messages_thread_seq", "thread_id", "seq"),
        Index("ix_messages_content_tsv", "content_tsv", postgresql_using="gin"),
        Index("ix_messages_user_created", "user_id", text("created_at DESC")),
    )


class MessageAttachment(Base):
    __tablename__ = "message_attachments"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )


class MessageAudio(Base, UUIDPrimaryKeyMixin):
    """Synthesised speech for a reply.

    Keyed so identical text in the same voice and language is never re-synthesised —
    TTS is the single largest controllable cost in the system.
    """

    __tablename__ = "message_audio"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    voice_id: Mapped[str] = mapped_column(String(100), nullable=False)
    lang: Mapped[str] = mapped_column(String(8), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    cache_hit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        Index("ix_message_audio_cache_key", "cache_key"),
        Index("ix_message_audio_message", "message_id"),
    )
