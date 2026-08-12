"""Server-Sent Events over a Redis stream.

Deltas are not pushed straight down the socket. They are appended to a Redis stream keyed
on the assistant message, and the HTTP connection tails that stream. Three things follow
from the indirection, and they are the whole reason for it:

* **A dropped connection resumes.** The browser sends `Last-Event-ID` automatically on
  reconnect; we replay from the entry after it. The reply is generated, and paid for, once.
* **Generation outlives the request.** If the reader disappears the writer keeps going, so
  a tunnel dropping at word thirty does not discard the answer.
* **Two readers are fine.** A second tab tailing the same message sees the same events.

Event ids are Redis stream ids (`1712-0`). They are opaque to the client, which only has to
echo the last one back.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any, Final

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

EVENT_DELTA: Final = "delta"
EVENT_DONE: Final = "done"
EVENT_ERROR: Final = "error"
TERMINAL_EVENTS: Final = frozenset({EVENT_DONE, EVENT_ERROR})

# Redis stream ids are monotonic; "0" means "everything from the beginning".
_FROM_START: Final = "0"
_MAX_ENTRIES: Final = 10_000


def stream_key(message_id: uuid.UUID) -> str:
    return f"stream:msg:{message_id}"


def lock_key(message_id: uuid.UUID) -> str:
    return f"genlock:{message_id}"


def cancel_key(message_id: uuid.UUID) -> str:
    return f"gencancel:{message_id}"


async def request_cancellation(
    redis: Redis[Any], settings: Settings, message_id: uuid.UUID
) -> None:
    """Ask whoever is generating to stop.

    A flag rather than task cancellation, because the generating coroutine may be on a
    different replica entirely. The generator checks it between deltas and stops
    cooperatively, keeping whatever it had already produced.
    """
    await redis.set(cancel_key(message_id), "1", ex=settings.generation_lock_seconds)


async def is_cancellation_requested(redis: Redis[Any], message_id: uuid.UUID) -> bool:
    return bool(await redis.exists(cancel_key(message_id)))


async def clear_cancellation(redis: Redis[Any], message_id: uuid.UUID) -> None:
    await redis.delete(cancel_key(message_id))


async def publish(
    redis: Redis[Any],
    settings: Settings,
    message_id: uuid.UUID,
    event: str,
    data: dict[str, Any],
) -> str:
    key = stream_key(message_id)
    entry_id = await redis.xadd(
        key,
        {"event": event, "data": json.dumps(data)},
        maxlen=_MAX_ENTRIES,
        approximate=True,
    )
    # Refreshed on every append so the window is measured from the last activity, not from
    # the first token of a long reply.
    await redis.expire(key, settings.sse_stream_ttl_seconds)
    return str(entry_id)


async def acquire_generation_lock(
    redis: Redis[Any], settings: Settings, message_id: uuid.UUID
) -> bool:
    """True if this caller should generate; False if someone else already is.

    Two tabs opening the same stream must not both call the model. The loser tails the
    stream and sees the winner's deltas.
    """
    acquired = await redis.set(
        lock_key(message_id), "1", nx=True, ex=settings.generation_lock_seconds
    )
    return bool(acquired)


async def release_generation_lock(redis: Redis[Any], message_id: uuid.UUID) -> None:
    await redis.delete(lock_key(message_id))


def frame(event: str, data: dict[str, Any], entry_id: str | None = None) -> str:
    """One SSE frame. The blank line is what ends it — omitting it hangs the client."""
    lines = []
    if entry_id:
        lines.append(f"id: {entry_id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {json.dumps(data)}")
    return "\n".join(lines) + "\n\n"


def heartbeat() -> str:
    """A comment frame. Keeps proxies from closing an idle connection."""
    return ": keep-alive\n\n"


async def tail(
    redis: Redis[Any],
    settings: Settings,
    message_id: uuid.UUID,
    *,
    last_event_id: str | None,
) -> AsyncIterator[str]:
    """Yield SSE frames from the stream until a terminal event, then stop.

    `last_event_id` is exclusive — Redis returns entries strictly after it — which is what
    makes a resume pick up where the connection died rather than repeating the last token.
    """
    key = stream_key(message_id)
    cursor = last_event_id or _FROM_START
    block_ms = int(settings.sse_heartbeat_seconds * 1000)
    elapsed = 0.0

    while elapsed < settings.sse_max_duration_seconds:
        batch = await redis.xread({key: cursor}, block=block_ms, count=100)

        if not batch:
            elapsed += settings.sse_heartbeat_seconds
            yield heartbeat()
            continue

        for _key, entries in batch:
            for entry_id, fields in entries:
                cursor = entry_id
                event = fields.get("event", EVENT_DELTA)
                data = json.loads(fields.get("data", "{}"))
                yield frame(event, data, entry_id)

                if event in TERMINAL_EVENTS:
                    return

    logger.warning("sse_max_duration_reached", message_id=str(message_id))
    yield frame(EVENT_ERROR, {"code": "stream_timeout", "message": "The stream timed out."})
