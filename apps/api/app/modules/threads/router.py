"""Conversations.

Generation is deliberately not here. This module's job is that a turn survives — the user
message and a `pending` assistant row are committed together, and the model runs afterwards
against rows that already exist. `GET /threads/{id}/stream` is the seam that work plugs
into; until it lands it answers with a typed error rather than a broken URL.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.core.deps import CurrentUser, ScopedSession, SettingsDep
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.core.redis import get_redis
from app.modules.threads import generation, service, stream, summarise
from app.modules.threads.schemas import (
    MessageAcceptedResponse,
    MessageCreate,
    MessageListResponse,
    MessageResponse,
    SearchResponse,
    SearchScope,
    SummaryResponse,
    ThreadCreate,
    ThreadListResponse,
    ThreadResponse,
    ThreadUpdate,
)

router = APIRouter()
search_router = APIRouter()

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Cursor = Annotated[str | None, Query(max_length=512)]


def _stream_url(thread_id: uuid.UUID, assistant_message_id: uuid.UUID) -> str:
    return f"/v1/threads/{thread_id}/stream?message_id={assistant_message_id}"


# ── threads ───────────────────────────────────────────────────────────────────
@router.get("", response_model=ThreadListResponse, summary="List conversations")
async def list_threads(
    db: ScopedSession,
    principal: CurrentUser,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
    include_archived: bool = False,
) -> ThreadListResponse:
    items, next_cursor = await service.list_threads(
        db,
        principal.user_id,
        limit=limit,
        cursor=cursor,
        include_archived=include_archived,
    )
    return ThreadListResponse(items=items, next_cursor=next_cursor)


@router.post(
    "",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a conversation",
)
async def create_thread(
    body: ThreadCreate, db: ScopedSession, principal: CurrentUser
) -> ThreadResponse:
    return await service.create_thread(db, principal.user_id, body)


@router.get("/{thread_id}", response_model=ThreadResponse, summary="Read a conversation")
async def read_thread(
    thread_id: uuid.UUID, db: ScopedSession, principal: CurrentUser
) -> ThreadResponse:
    return await service.get_thread(db, principal.user_id, thread_id)


@router.patch(
    "/{thread_id}",
    response_model=ThreadResponse,
    summary="Rename, pin or archive a conversation",
)
async def update_thread(
    thread_id: uuid.UUID, patch: ThreadUpdate, db: ScopedSession, principal: CurrentUser
) -> ThreadResponse:
    return await service.update_thread(db, principal.user_id, thread_id, patch)


@router.delete(
    "/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a conversation and its messages",
)
async def delete_thread(
    thread_id: uuid.UUID, db: ScopedSession, principal: CurrentUser
) -> Response:
    await service.delete_thread(db, principal.user_id, thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── messages ──────────────────────────────────────────────────────────────────
@router.get(
    "/{thread_id}/messages",
    response_model=MessageListResponse,
    summary="Read a conversation's messages",
)
async def list_messages(
    thread_id: uuid.UUID,
    db: ScopedSession,
    principal: CurrentUser,
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> MessageListResponse:
    items, next_cursor = await service.list_messages(
        db, principal.user_id, thread_id, limit=limit, cursor=cursor
    )
    return MessageListResponse(items=items, next_cursor=next_cursor)


@router.post(
    "/{thread_id}/messages",
    response_model=MessageAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a turn",
)
async def create_message(
    thread_id: uuid.UUID, body: MessageCreate, db: ScopedSession, principal: CurrentUser
) -> MessageAcceptedResponse:
    user_message, assistant_message, replayed = await service.create_message(
        db, principal.user_id, thread_id, body
    )
    return MessageAcceptedResponse(
        message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        seq=user_message.seq,
        stream_url=_stream_url(thread_id, assistant_message.id),
        idempotent_replay=replayed,
    )


@router.post(
    "/{thread_id}/messages/{message_id}/cancel",
    response_model=MessageResponse,
    summary="Stop a reply that is being written",
)
async def cancel_message(
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    db: ScopedSession,
    principal: CurrentUser,
    settings: SettingsDep,
) -> MessageResponse:
    await service.get_thread(db, principal.user_id, thread_id)
    message = await generation.cancel_generation(
        db, settings, user_id=principal.user_id, thread_id=thread_id, message_id=message_id
    )
    return MessageResponse.model_validate(message)


@router.post(
    "/{thread_id}/messages/{message_id}/regenerate",
    response_model=MessageAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Try that reply again",
)
async def regenerate_message(
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    db: ScopedSession,
    principal: CurrentUser,
    settings: SettingsDep,
) -> MessageAcceptedResponse:
    await service.get_thread(db, principal.user_id, thread_id)
    replacement = await generation.regenerate(
        db, settings, user_id=principal.user_id, thread_id=thread_id, message_id=message_id
    )
    return MessageAcceptedResponse(
        message_id=message_id,
        assistant_message_id=replacement.id,
        seq=replacement.seq,
        stream_url=_stream_url(thread_id, replacement.id),
    )


@router.post(
    "/{thread_id}/summarise",
    response_model=SummaryResponse,
    summary="Compress the earlier part of this conversation now",
)
async def summarise_thread(
    thread_id: uuid.UUID,
    db: ScopedSession,
    principal: CurrentUser,
    settings: SettingsDep,
) -> SummaryResponse:
    await service.get_thread(db, principal.user_id, thread_id)
    summarised = await summarise.maybe_summarise(
        db, settings, user_id=principal.user_id, thread_id=thread_id, force=True
    )
    thread = await service.get_thread_row(db, principal.user_id, thread_id)
    return SummaryResponse(
        summary=thread.summary,
        summary_upto_seq=thread.summary_upto_seq,
        summarised=summarised,
    )


@router.get(
    "/{thread_id}/stream",
    summary="Stream the reply for a pending turn",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_reply(
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    request: Request,
    db: ScopedSession,
    principal: CurrentUser,
    settings: SettingsDep,
) -> StreamingResponse:
    """Server-Sent Events: `delta` frames, then one `done` or `error`.

    Reconnect by sending `Last-Event-ID` — the browser's `EventSource` does it for you —
    and the stream resumes from the entry after it rather than regenerating. Ownership is
    checked before a single byte is written, because once the status line is sent an error
    can only be reported inside the stream.
    """
    await service.get_thread(db, principal.user_id, thread_id)

    finished = await generation.replay_terminal_state(
        db,
        settings,
        user_id=principal.user_id,
        thread_id=thread_id,
        message_id=message_id,
    )
    last_event_id = request.headers.get("last-event-id")

    async def events() -> AsyncIterator[str]:
        # Already finished and the Redis window may be long gone: answer from Postgres,
        # which is the durable copy, rather than waiting for deltas that will never come.
        if finished is not None and not last_event_id:
            yield stream.frame(
                stream.EVENT_DELTA if finished.status == "complete" else stream.EVENT_ERROR,
                {"text": finished.content}
                if finished.status == "complete"
                else (finished.error or {"code": "generation_failed"}),
            )
            yield stream.frame(
                stream.EVENT_DONE,
                {
                    "message_id": str(finished.id),
                    "finish_reason": finished.finish_reason,
                    "tokens": {
                        "in": finished.input_tokens,
                        "out": finished.output_tokens,
                    },
                },
            )
            return

        await generation.ensure_generation_started(
            settings,
            user_id=principal.user_id,
            thread_id=thread_id,
            message_id=message_id,
        )
        async for chunk in stream.tail(
            get_redis(), settings, message_id, last_event_id=last_event_id
        ):
            yield chunk

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            # Starlette's GZipMiddleware buffers until `minimum_size` bytes have
            # accumulated, which would hold the first tokens back; it skips any response
            # that already declares an encoding. nginx needs the second header for the
            # same reason.
            "Content-Encoding": "identity",
            "X-Accel-Buffering": "no",
        },
    )


# ── search ────────────────────────────────────────────────────────────────────
@search_router.get("", response_model=SearchResponse, summary="Search conversations")
async def search(
    db: ScopedSession,
    principal: CurrentUser,
    q: Annotated[str, Query(min_length=1, max_length=200)],
    scope: SearchScope = "messages",
    limit: Limit = DEFAULT_LIMIT,
    cursor: Cursor = None,
) -> SearchResponse:
    items, next_cursor = await service.search(
        db, principal.user_id, query_text=q, scope=scope, limit=limit, cursor=cursor
    )
    return SearchResponse(scope=scope, items=items, next_cursor=next_cursor)


__all__ = ["MessageResponse", "router", "search_router"]
