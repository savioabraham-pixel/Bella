"""Identity, profile and session tables.

`users` holds the identity; `profiles` holds the personal data. They are separate because
they have different retention rules, different access controls, and because an erasure
request drops the profile while audit needs the (pseudonymised) identity row to survive.

This is where the 19 hardcoded biographies in the legacy `index.html` land — as rows, owned
by the person they describe, readable only by them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.db.models.conversations import Thread
    from app.db.models.memory import Memory

USER_STATUSES = ("active", "suspended", "deletion_requested", "deleted")
USER_ROLES = ("member", "admin", "owner")
SENSITIVITY_LEVELS = ("normal", "high")
THEMES = ("light", "dark", "system")
RELATION_KINDS = ("spouse", "child", "parent", "sibling", "friend", "colleague", "other")
RETENTION_DAYS = (30, 90, 365)


class User(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "users"

    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(CITEXT, unique=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    phone_e164: Mapped[str | None] = mapped_column(String(20), unique=True)
    phone_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Human-quotable identifier shown in support conversations ("BE-12345678").
    display_id: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default=text("'active'")
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="member", server_default=text("'member'")
    )

    # Minors need verifiable guardian consent under the DPDP Act — two current users are
    # in Grade 7 and one is a toddler, so this is modelled from day one rather than bolted on.
    is_minor: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    guardian_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    locale: Mapped[str] = mapped_column(
        String(16), nullable=False, default="en-IN", server_default=text("'en-IN'")
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Asia/Kolkata",
        server_default=text("'Asia/Kolkata'"),
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[Profile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    relationships_: Mapped[list[Relationship]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="Relationship.user_id",
    )
    sessions: Mapped[list[Session]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    threads: Mapped[list[Thread]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    memories: Mapped[list[Memory]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(f"status IN {USER_STATUSES}", name="status_valid"),
        CheckConstraint(f"role IN {USER_ROLES}", name="role_valid"),
        Index("ix_users_status", "status", postgresql_where=text("deleted_at IS NULL")),
    )


class Profile(Base, TimestampMixin):
    """Personal data. Separated from `users` so it can be encrypted, exported or dropped
    independently of the identity row."""

    __tablename__ = "profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    full_name: Mapped[str | None] = mapped_column(String(200))
    preferred_name: Mapped[str | None] = mapped_column(String(100))
    pronunciation: Mapped[str | None] = mapped_column(String(200))
    pronouns: Mapped[str | None] = mapped_column(String(50))
    gender: Mapped[str | None] = mapped_column(String(50))
    date_of_birth: Mapped[dt.date | None] = mapped_column(Date)

    # GCS object key, never a URL — URLs are minted short-lived on read.
    avatar_object: Mapped[str | None] = mapped_column(String(512))

    # Replaces the hardcoded BACKGROUNDS / USER_CTX blobs.
    bio: Mapped[str | None] = mapped_column(Text)
    group_name: Mapped[str | None] = mapped_column(String(100))

    tone_preferences: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    user: Mapped[User] = relationship(back_populates="profile")


class Relationship(Base, UUIDPrimaryKeyMixin):
    """People Bella may ask about — replaces the hardcoded FOLLOWUP map."""

    __tablename__ = "relationships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    related_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    relation: Mapped[str] = mapped_column(String(50), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    ask_about: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    sensitivity: Mapped[str] = mapped_column(
        String(16), nullable=False, default="normal", server_default=text("'normal'")
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    user: Mapped[User] = relationship(back_populates="relationships_", foreign_keys=[user_id])

    __table_args__ = (
        CheckConstraint(f"sensitivity IN {SENSITIVITY_LEVELS}", name="sensitivity_valid"),
        Index(
            "ix_relationships_user_ask",
            "user_id",
            postgresql_where=text("ask_about"),
        ),
    )


class Session(Base, UUIDPrimaryKeyMixin):
    """Refresh-token sessions.

    The token itself is never stored — only a SHA-256 hash. `family_id` tracks a rotation
    lineage: presenting an already-rotated token means the token was stolen, and the whole
    family is revoked.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    refresh_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    device_name: Mapped[str | None] = mapped_column(String(200))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (
        Index(
            "ix_sessions_user_active",
            "user_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("ix_sessions_token_hash", "refresh_token_hash", unique=True),
        Index("ix_sessions_family", "family_id"),
    )


class UserPreferences(Base):
    """How the app behaves for this person.

    A side table rather than columns on `profiles`, mirroring `user_quotas`: these are
    operational settings, not personal data, and they must survive a profile erasure long
    enough for the retention job to know what the person asked for.

    Typed columns rather than a JSONB blob because `message_retention_days` is read by a
    background sweep across all users — a predicate JSONB cannot index usefully — and
    because the legacy client's habit of storing whatever it liked under `b_pref_*` is
    exactly what this replaces.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    theme: Mapped[str] = mapped_column(
        String(16), nullable=False, default="system", server_default=text("'system'")
    )
    clock_24h: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    voice_replies: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    voice_autoplay: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    hands_free: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    notifications_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    location_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # NULL means "keep forever", which is the default and the reverse of the legacy
    # behaviour where closing a tab destroyed the conversation (finding H2).
    message_retention_days: Mapped[int | None] = mapped_column(Integer)

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()"), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(f"theme IN {THEMES}", name="theme_valid"),
        CheckConstraint(
            f"message_retention_days IS NULL OR message_retention_days IN {RETENTION_DAYS}",
            name="retention_valid",
        ),
    )


class UserQuota(Base):
    """Server-side quotas. The legacy client computed these in the browser and reported
    thread count under a label that said 'memories' — this is the source of truth."""

    __tablename__ = "user_quotas"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    memories_used: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    memories_limit: Mapped[int] = mapped_column(
        nullable=False, default=1000, server_default=text("1000")
    )
    threads_used: Mapped[int] = mapped_column(nullable=False, default=0, server_default=text("0"))
    threads_limit: Mapped[int] = mapped_column(
        nullable=False, default=500, server_default=text("500")
    )
    # BigInteger, not Integer: the 2 GiB default is 2147483648, one past the int4 ceiling,
    # so every quota row would fail to insert. Byte counts get 64 bits on principle.
    storage_bytes_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    storage_limit_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=2 * 1024**3, server_default=text("2147483648")
    )
    tokens_used_today: Mapped[int] = mapped_column(
        nullable=False, default=0, server_default=text("0")
    )
    tokens_limit_daily: Mapped[int] = mapped_column(
        nullable=False, default=1_000_000, server_default=text("1000000")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
