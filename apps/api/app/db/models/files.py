"""Uploaded files and their extracted chunks.

Extraction moves off the client. That fixes three things at once: pdf.js and SheetJS stop
running on a phone, scanned PDFs can go through OCR instead of returning
"[PDF appears scanned]", and extracted text is reusable across threads rather than
re-parsed and truncated at 15,000 characters per message.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
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
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.db.models.memory import EMBEDDING_DIMENSIONS

FILE_KINDS = ("document", "image", "audio", "video", "recording")
FILE_STATUSES = (
    "uploading",
    "scanning",
    "extracting",
    "ready",
    "failed",
    "quarantined",
)


class File(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "files"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("threads.id", ondelete="SET NULL")
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    # Sniffed from the bytes server-side. The client-supplied extension is advisory only.
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)

    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="uploading", server_default=text("'uploading'")
    )
    scan_result: Mapped[str | None] = mapped_column(String(100))

    page_count: Mapped[int | None] = mapped_column(Integer)
    extracted_chars: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    chunks: Mapped[list[FileChunk]] = relationship(
        back_populates="file", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(f"kind IN {FILE_KINDS}", name="kind_valid"),
        CheckConstraint(f"status IN {FILE_STATUSES}", name="status_valid"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        # Re-uploading the same document costs nothing the second time.
        Index(
            "ix_files_user_sha256",
            "user_id",
            "sha256",
            unique=True,
            postgresql_where=text("status = 'ready'"),
        ),
        Index("ix_files_user_created", "user_id", text("created_at DESC")),
        Index(
            "ix_files_expiry",
            "expires_at",
            postgresql_where=text("expires_at IS NOT NULL"),
        ),
    )


class FileChunk(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "file_chunks"

    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', content)", persisted=True)
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(100))

    page_from: Mapped[int | None] = mapped_column(Integer)
    page_to: Mapped[int | None] = mapped_column(Integer)
    token_count: Mapped[int | None] = mapped_column(Integer)

    file: Mapped[File] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("file_id", "chunk_index", name="uq_file_chunks_file_id_chunk_index"),
        Index(
            "ix_file_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_file_chunks_content_tsv", "content_tsv", postgresql_using="gin"),
        Index("ix_file_chunks_user", "user_id"),
    )
