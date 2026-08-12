"""Conversation persistence.

The property this module exists to guarantee: **a turn is durable before the model is
called.** The legacy client held history in `localStorage`, truncated to 20 messages, and
wiped it on `pagehide` — so a crash, a refresh or a closed tab destroyed the conversation
(finding H2). Here the user message and a `pending` assistant row are committed together,
and generation happens afterwards against rows that already exist.

Sessions arrive already RLS-scoped from `ScopedSession`. Queries still say
`WHERE user_id = ...` because a policy is a backstop, not a substitute for meaning it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    ColumnElement,
    DateTime,
    Select,
    Uuid,
    func,
    literal,
    select,
    tuple_,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
    QuotaExceededError,
)
from app.core.logging import get_logger
from app.core.pagination import decode_cursor, encode_cursor, parse_datetime
from app.db.models.conversations import Message, Thread
from app.db.models.identity import UserQuota
from app.modules.threads.schemas import (
    MessageCreate,
    MessageResponse,
    SearchHit,
    ThreadCreate,
    ThreadResponse,
    ThreadUpdate,
)

logger = get_logger(__name__)

# Postgres text-search configuration. 'simple' matches the generated column on `messages`
# and is deliberate: the family speaks four languages, and an English stemmer would mangle
# Hindi, Bengali and Marathi. The cost is no stemming — "running" does not match "run".
_TS_CONFIG = "simple"
_SNIPPET_OPTIONS = "MaxWords=28, MinWords=8, ShortWord=2, HighlightAll=FALSE"


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _thread_sort_key() -> Any:
    """A thread with no messages yet still has to sort somewhere sensible."""
    return func.coalesce(Thread.last_message_at, Thread.created_at)


# Cursor components are bound as explicitly typed literals. Inside a row comparison the
# driver has no column to infer a parameter type from, so an untyped bind would be sent as
# text and Postgres would refuse to compare it against a uuid or a timestamptz.
def _bool_literal(value: object) -> ColumnElement[bool]:
    return literal(bool(value), Boolean)


def _timestamp_literal(value: object) -> ColumnElement[dt.datetime]:
    return literal(parse_datetime(value), DateTime(timezone=True))


def _uuid_literal(value: object) -> ColumnElement[uuid.UUID]:
    try:
        parsed = uuid.UUID(str(value))
    except ValueError as exc:
        raise BadRequestError("That cursor is not valid.", code="invalid_cursor") from exc
    return literal(parsed, Uuid(as_uuid=True))


def _to_thread_response(thread: Thread) -> ThreadResponse:
    return ThreadResponse(
        id=thread.id,
        title=thread.title,
        title_generated=thread.title_generated,
        pinned=thread.pinned,
        archived=thread.archived_at is not None,
        message_count=thread.message_count,
        last_message_at=thread.last_message_at,
        created_at=thread.created_at,
    )


# ── threads ───────────────────────────────────────────────────────────────────
async def list_threads(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None,
    include_archived: bool,
) -> tuple[list[ThreadResponse], str | None]:
    """Pinned first, then most recently active.

    Keyset paginated on the whole sort key, so a thread that receives a message while the
    user is paging cannot cause a duplicate or a skipped row.
    """
    sort_key = _thread_sort_key()
    query: Select[Any] = select(Thread, sort_key.label("sort_at")).where(Thread.user_id == user_id)
    if not include_archived:
        query = query.where(Thread.archived_at.is_(None))

    if cursor:
        pinned, sort_at, thread_id = decode_cursor(cursor)
        query = query.where(
            tuple_(Thread.pinned, sort_key, Thread.id)
            < tuple_(_bool_literal(pinned), _timestamp_literal(sort_at), _uuid_literal(thread_id))
        )

    query = query.order_by(Thread.pinned.desc(), sort_key.desc(), Thread.id.desc()).limit(limit + 1)

    rows = (await db.execute(query)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = None
    if has_more and rows:
        last_thread, last_sort_at = rows[-1]
        next_cursor = encode_cursor([last_thread.pinned, last_sort_at, last_thread.id])

    return [_to_thread_response(thread) for thread, _ in rows], next_cursor


async def create_thread(db: AsyncSession, user_id: uuid.UUID, body: ThreadCreate) -> ThreadResponse:
    await _assert_thread_quota(db, user_id)

    thread = Thread(user_id=user_id)
    if body.title:
        thread.title = body.title
    db.add(thread)
    await db.flush()

    logger.info("thread_created")
    return _to_thread_response(thread)


async def _assert_thread_quota(db: AsyncSession, user_id: uuid.UUID) -> None:
    quota = (
        await db.execute(select(UserQuota).where(UserQuota.user_id == user_id))
    ).scalar_one_or_none()
    limit = quota.threads_limit if quota else 500

    used = (
        await db.execute(select(func.count()).select_from(Thread).where(Thread.user_id == user_id))
    ).scalar_one()

    if used >= limit:
        raise QuotaExceededError(
            "You have reached your conversation limit. Delete a conversation to start a new one.",
            code="thread_quota_exceeded",
            details={"used": used, "limit": limit},
        )


async def _get_thread(db: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID) -> Thread:
    thread = (
        await db.execute(select(Thread).where(Thread.id == thread_id, Thread.user_id == user_id))
    ).scalar_one_or_none()
    if thread is None:
        # 404 rather than 403 even when the row exists under another user — a 403 would
        # confirm the id is real.
        raise NotFoundError("No conversation with that id.", code="thread_not_found")
    return thread


async def get_thread(db: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID) -> ThreadResponse:
    return _to_thread_response(await _get_thread(db, user_id, thread_id))


async def get_thread_row(db: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID) -> Thread:
    """The ORM row, for callers that need fields the response model does not carry."""
    return await _get_thread(db, user_id, thread_id)


async def update_thread(
    db: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID, patch: ThreadUpdate
) -> ThreadResponse:
    thread = await _get_thread(db, user_id, thread_id)
    changes = patch.model_dump(exclude_unset=True)

    if "title" in changes and changes["title"] is not None:
        thread.title = changes["title"]
        # A hand-written title must not be overwritten by the auto-titler later.
        thread.title_generated = False
    if "pinned" in changes and changes["pinned"] is not None:
        thread.pinned = changes["pinned"]
    if "archived" in changes and changes["archived"] is not None:
        thread.archived_at = _now() if changes["archived"] else None

    await db.flush()
    return _to_thread_response(thread)


async def delete_thread(db: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID) -> None:
    """A real delete. Messages and attachments cascade.

    Archiving is the reversible option and is what the UI should offer first; this is for
    people who mean it, and it has to actually remove the rows for an erasure request to
    be honest.
    """
    thread = await _get_thread(db, user_id, thread_id)
    await db.delete(thread)
    await db.flush()
    logger.info("thread_deleted")


# ── messages ──────────────────────────────────────────────────────────────────
async def list_messages(
    db: AsyncSession,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    limit: int,
    cursor: str | None,
) -> tuple[list[MessageResponse], str | None]:
    """Oldest first, so a client appends as it pages — the order a transcript reads in."""
    await _get_thread(db, user_id, thread_id)

    query = select(Message).where(Message.thread_id == thread_id, Message.user_id == user_id)
    if cursor:
        (after_seq,) = decode_cursor(cursor)
        query = query.where(Message.seq > int(after_seq))

    query = query.order_by(Message.seq).limit(limit + 1)

    rows = list((await db.execute(query)).scalars().all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    next_cursor = encode_cursor([rows[-1].seq]) if has_more and rows else None
    return [MessageResponse.model_validate(row) for row in rows], next_cursor


async def _find_by_client_id(
    db: AsyncSession, user_id: uuid.UUID, client_message_id: str
) -> Message | None:
    return (
        await db.execute(
            select(Message).where(
                Message.user_id == user_id, Message.client_message_id == client_message_id
            )
        )
    ).scalar_one_or_none()


async def _paired_assistant_message(db: AsyncSession, user_message: Message) -> Message:
    """The assistant row answering a user turn — the newest one.

    Normally that is simply the next seq. After a regenerate it is not: the superseded
    attempt keeps its place in the transcript and a fresh row is appended, so "which reply
    belongs to this turn" has to mean the latest, not the first.
    """
    assistant = (
        await db.execute(
            select(Message)
            .where(
                Message.thread_id == user_message.thread_id,
                Message.seq > user_message.seq,
                Message.role == "assistant",
            )
            .order_by(Message.seq.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if assistant is None:  # pragma: no cover — written in the same transaction
        raise ConflictError("That turn is in an inconsistent state.", code="turn_incomplete")
    return assistant


async def create_message(
    db: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID, body: MessageCreate
) -> tuple[Message, Message, bool]:
    """Persist a user turn and the assistant row that will answer it.

    Returns `(user_message, assistant_message, was_replay)`. Generation is a separate step
    that runs against the assistant row; nothing here calls a provider.
    """
    if body.client_message_id:
        existing = await _find_by_client_id(db, user_id, body.client_message_id)
        if existing is not None:
            logger.info("message_idempotent_replay")
            return existing, await _paired_assistant_message(db, existing), True

    # Lock the thread row for the rest of the transaction. Two concurrent sends would
    # otherwise compute the same `seq` and one would lose to the unique constraint.
    thread = (
        await db.execute(
            select(Thread)
            .where(Thread.id == thread_id, Thread.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if thread is None:
        raise NotFoundError("No conversation with that id.", code="thread_not_found")

    next_seq = (
        await db.execute(
            select(func.coalesce(func.max(Message.seq), 0) + 1).where(
                Message.thread_id == thread_id
            )
        )
    ).scalar_one()

    user_message = Message(
        thread_id=thread_id,
        user_id=user_id,
        seq=next_seq,
        role="user",
        content=body.content,
        status="complete",
        client_message_id=body.client_message_id,
        language=body.options.language,
    )
    assistant_message = Message(
        thread_id=thread_id,
        user_id=user_id,
        seq=next_seq + 1,
        role="assistant",
        content="",
        status="pending",
    )
    try:
        # A SAVEPOINT, not the enclosing transaction: `app.user_id` is set transaction-local
        # and a full rollback would discard it, failing the RLS check on every statement
        # that follows.
        async with db.begin_nested():
            db.add_all([user_message, assistant_message])
            await db.flush()
    except IntegrityError:
        # Two identical sends raced past the lookup above. The winner's rows are the
        # answer; this caller gets them rather than an error.
        if not body.client_message_id:
            raise
        existing = await _find_by_client_id(db, user_id, body.client_message_id)
        if existing is None:  # pragma: no cover — the constraint that fired says otherwise
            raise
        return existing, await _paired_assistant_message(db, existing), True

    thread.message_count = next_seq + 1
    thread.last_message_at = _now()
    await db.flush()

    logger.info("turn_persisted", seq=next_seq)
    return user_message, assistant_message, False


# ── search ────────────────────────────────────────────────────────────────────
async def search(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    query_text: str,
    scope: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[SearchHit], str | None]:
    """Full-text search across the caller's own conversations.

    Replaces the legacy client-side substring scan, which could only see the threads that
    happened to be loaded in the browser and missed everything older than the 20-message
    localStorage window.
    """
    tsquery = func.plainto_tsquery(_TS_CONFIG, query_text)

    if scope == "threads":
        statement = (
            select(
                Thread.id.label("thread_id"),
                Thread.title.label("thread_title"),
                Thread.created_at.label("created_at"),
            )
            .where(
                Thread.user_id == user_id,
                func.to_tsvector(_TS_CONFIG, Thread.title).op("@@")(tsquery),
            )
            .order_by(Thread.created_at.desc(), Thread.id.desc())
        )
        if cursor:
            created_at, thread_id = decode_cursor(cursor)
            statement = statement.where(
                tuple_(Thread.created_at, Thread.id)
                < tuple_(_timestamp_literal(created_at), _uuid_literal(thread_id))
            )

        rows = (await db.execute(statement.limit(limit + 1))).mappings().all()
        has_more = len(rows) > limit
        rows = rows[:limit]

        hits = [
            SearchHit(
                thread_id=row["thread_id"],
                thread_title=row["thread_title"],
                snippet=row["thread_title"],
                created_at=row["created_at"],
            )
            for row in rows
        ]
        next_cursor = (
            encode_cursor([rows[-1]["created_at"], rows[-1]["thread_id"]])
            if has_more and rows
            else None
        )
        return hits, next_cursor

    statement = (
        select(
            Message.id.label("message_id"),
            Message.thread_id.label("thread_id"),
            Message.seq.label("seq"),
            Message.role.label("role"),
            Message.created_at.label("created_at"),
            Thread.title.label("thread_title"),
            func.ts_headline(_TS_CONFIG, Message.content, tsquery, _SNIPPET_OPTIONS).label(
                "snippet"
            ),
        )
        .join(Thread, Thread.id == Message.thread_id)
        .where(
            Message.user_id == user_id,
            Message.content_tsv.op("@@")(tsquery),
            # An empty assistant placeholder has nothing to find and would only be noise.
            Message.status == "complete",
        )
        .order_by(Message.created_at.desc(), Message.id.desc())
    )
    if cursor:
        created_at, message_id = decode_cursor(cursor)
        statement = statement.where(
            tuple_(Message.created_at, Message.id)
            < tuple_(_timestamp_literal(created_at), _uuid_literal(message_id))
        )

    rows = (await db.execute(statement.limit(limit + 1))).mappings().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    hits = [
        SearchHit(
            thread_id=row["thread_id"],
            thread_title=row["thread_title"],
            message_id=row["message_id"],
            seq=row["seq"],
            role=row["role"],
            snippet=row["snippet"],
            created_at=row["created_at"],
        )
        for row in rows
    ]
    next_cursor = (
        encode_cursor([rows[-1]["created_at"], rows[-1]["message_id"]])
        if has_more and rows
        else None
    )
    return hits, next_cursor
