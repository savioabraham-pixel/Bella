"""Turning a pending assistant row into an answer.

The row already exists when this runs — that is the whole design. Every outcome here is a
state transition on a durable record rather than something that happens to an HTTP response
in flight:

    pending  → streaming → complete     the model answered
    pending  → streaming → failed       the provider failed; the turn survives, retryable
    complete →            complete      a repeat request returns what was already produced

Nothing in this module deletes anything on failure. The legacy client dropped the user's
message on any error, which is how a question asked at 2am simply ceased to exist.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError, ConflictError, NotFoundError, ProviderError
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.db.models.conversations import Message, Thread
from app.db.models.identity import Profile, User
from app.db.session import session_for_user
from app.modules.threads import stream
from app.modules.threads.prompt import (
    active_template,
    build_system_prompt,
    fit_to_budget,
    load_history,
    render,
    to_turns,
)
from app.modules.threads.summarise import maybe_summarise
from app.providers.base import GenerationRequest, GenerationResult, ResultAccumulator
from app.providers.registry import generate as call_provider
from app.providers.registry import get_llm_provider
from app.providers.registry import stream as provider_stream

logger = get_logger(__name__)

_TITLE_MAX_CHARS = 60
TITLE_PROMPT_NAME = "bella.title"
DEFAULT_TITLE = "New Conversation"

# How often the cancellation flag is consulted, in deltas.
_CANCEL_CHECK_EVERY = 8


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


async def _load_assistant_message(
    db: AsyncSession, user_id: uuid.UUID, thread_id: uuid.UUID, message_id: uuid.UUID
) -> Message:
    message = (
        await db.execute(
            select(Message).where(
                Message.id == message_id,
                Message.thread_id == thread_id,
                Message.user_id == user_id,
                Message.role == "assistant",
            )
        )
    ).scalar_one_or_none()
    if message is None:
        raise NotFoundError("No such reply.", code="message_not_found")
    return message


def _truncated_title(content: str) -> str:
    """Fallback when the model cannot be reached. Better than "New Conversation"."""
    collapsed = " ".join(content.split())
    if len(collapsed) <= _TITLE_MAX_CHARS:
        return collapsed
    return collapsed[: _TITLE_MAX_CHARS - 1].rstrip() + "\u2026"


def _clean_title(raw: str) -> str:
    """Models like to answer a request for a title with a sentence about the title."""
    title = " ".join(raw.split()).strip().strip("\"'\u201c\u201d").rstrip(".")
    return title[:_TITLE_MAX_CHARS].strip()


async def maybe_title_thread(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> str | None:
    """Name a thread from its opening exchange, once.

    Runs after the reply has been delivered, so the model call costs the user nothing in
    waiting. A title the person typed themselves is never overwritten — `title_generated`
    is what records the difference.
    """
    thread = (await db.execute(select(Thread).where(Thread.id == thread_id))).scalar_one_or_none()
    if thread is None or thread.title_generated or thread.title != DEFAULT_TITLE:
        return None

    history = await load_history(
        db, settings, thread_id=thread_id, user_id=user_id, upto_seq=thread.message_count + 1
    )
    if not history:
        return None

    opening = to_turns(history)[:2]
    fallback = _truncated_title(opening[0].content)

    try:
        version = await active_template(db, TITLE_PROMPT_NAME)
        result = await call_provider(
            settings,
            GenerationRequest(
                system=render(version.template),
                turns=opening,
                model=settings.llm_model,
                max_output_tokens=settings.llm_title_max_output_tokens,
                # Naming is not a creative act; the same conversation should get the same
                # title whenever this runs.
                temperature=0.0,
            ),
        )
        title = _clean_title(result.text) or fallback
    except AppError as exc:
        # A thread with a mediocre title is a far smaller problem than a reply that fails
        # because naming it did.
        logger.warning("titling_failed", code=exc.code)
        title = fallback

    if not title or title == DEFAULT_TITLE:
        return None

    thread.title = title
    thread.title_generated = True
    await db.flush()
    logger.info("thread_titled")
    return title


async def _run(
    settings: Settings,
    request: GenerationRequest,
    *,
    message_id: uuid.UUID,
    publish_to: Redis[Any] | None,
) -> GenerationResult:
    """Drive the provider, mirroring each delta into Redis when a reader may be tailing.

    With no `publish_to` this is the plain aggregation — the same code path, minus the
    fan-out — so a caller that does not care about streaming pays nothing for it.
    """
    if publish_to is None:
        return await call_provider(settings, request)

    # `provider` is recorded as the incumbent. Under failover the switch is in the log
    # rather than here — this field feeds a log line, not a decision.
    accumulator = ResultAccumulator()
    seen = 0

    async for delta in provider_stream(settings, request):
        accumulator.add(delta)
        if delta.text:
            await stream.publish(
                publish_to, settings, message_id, stream.EVENT_DELTA, {"text": delta.text}
            )

        # Checked periodically rather than per delta: a Redis round trip between every
        # token would show up in time-to-completion for no benefit, and stopping a few
        # words late is invisible to someone who has just pressed Stop.
        seen += 1
        if seen % _CANCEL_CHECK_EVERY == 0 and await stream.is_cancellation_requested(
            publish_to, message_id
        ):
            accumulator.finish_reason = "cancelled"
            logger.info("generation_cancelled", message_id=str(message_id))
            break

    return accumulator.finish(request.model, get_llm_provider(settings).name)


async def generate_reply(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
    publish_to: Redis[Any] | None = None,
) -> Message:
    """Produce the reply for one pending assistant row.

    Safe to call twice: a row that already reached a terminal state is returned as it
    stands rather than regenerated, so a client that retries a dropped connection does not
    pay for a second answer.
    """
    assistant = await _load_assistant_message(db, user_id, thread_id, message_id)

    if assistant.status in ("complete", "cancelled"):
        return assistant

    thread = (
        await db.execute(select(Thread).where(Thread.id == thread_id).with_for_update())
    ).scalar_one()

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()

    system, prompt_version_id = await build_system_prompt(
        db, user, profile, conversation_summary=thread.summary
    )
    history = await load_history(
        db,
        settings,
        thread_id=thread_id,
        user_id=user_id,
        upto_seq=assistant.seq - 1,
        # Turns already represented by the summary must not also be sent verbatim.
        after_seq=thread.summary_upto_seq,
    )
    turns = fit_to_budget(system, to_turns(history), settings.llm_context_budget_tokens)
    if not turns:
        raise NotFoundError("There is nothing to reply to.", code="empty_turn")

    assistant.status = "streaming"
    assistant.prompt_version_id = prompt_version_id
    await db.flush()

    request = GenerationRequest(
        system=system,
        turns=turns,
        model=settings.llm_model,
        max_output_tokens=settings.llm_max_output_tokens,
        temperature=settings.llm_temperature,
    )

    try:
        result = await _run(settings, request, message_id=assistant.id, publish_to=publish_to)
    except ProviderError as exc:
        # The turn survives. `status='failed'` is what makes the retry real rather than a
        # promise — the user's question is still on the server either way.
        #
        # Committed rather than flushed, and committed *before* the error propagates: the
        # request-scoped session rolls back when an exception leaves the route, which
        # would take this record with it and leave the row stuck on `pending` forever.
        assistant.status = "failed"
        assistant.error = {"code": exc.code, "message": exc.message}
        await db.commit()
        logger.warning("generation_failed", code=exc.code)
        raise
    except AppError as exc:
        assistant.status = "failed"
        assistant.error = {"code": exc.code, "message": exc.message}
        await db.commit()
        raise

    assistant.content = result.text
    # A cancelled reply keeps whatever it had already produced. Discarding it would throw
    # away work the user has already watched appear on screen.
    assistant.status = "cancelled" if result.finish_reason == "cancelled" else "complete"
    assistant.model = result.model
    assistant.input_tokens = result.input_tokens
    assistant.output_tokens = result.output_tokens
    assistant.finish_reason = result.finish_reason
    assistant.latency_ms = result.latency_ms
    assistant.first_token_ms = result.first_token_ms

    thread.last_message_at = _now()

    await db.flush()
    logger.info(
        "generation_complete",
        provider=result.provider,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
    )
    return assistant


# Detached tasks are kept here because asyncio holds only a weak reference to a task: drop
# it and the generation can be garbage-collected mid-reply.
_running: set[asyncio.Task[None]] = set()


async def _generate_detached(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
) -> None:
    """Generate on a session of its own, publishing to Redis as it goes.

    Deliberately not tied to the request that started it. If the reader disconnects at word
    thirty the writer keeps going, the row still reaches `complete`, and a reconnect replays
    the whole reply from the stream instead of paying for a second one.
    """
    redis = get_redis()
    try:
        async with session_for_user(user_id) as db:
            message = await generate_reply(
                db,
                settings,
                user_id=user_id,
                thread_id=thread_id,
                message_id=message_id,
                publish_to=redis,
            )
            payload = {
                "message_id": str(message.id),
                "finish_reason": message.finish_reason,
                "tokens": {"in": message.input_tokens, "out": message.output_tokens},
            }
        await stream.publish(redis, settings, message_id, stream.EVENT_DONE, payload)

        # Everything below runs after the reader already has the reply. Naming and
        # compressing are each worth a model call; neither is worth making someone wait.
        async with session_for_user(user_id) as db:
            await maybe_title_thread(db, settings, user_id=user_id, thread_id=thread_id)
            await maybe_summarise(db, settings, user_id=user_id, thread_id=thread_id)
    except AppError as exc:
        await stream.publish(
            redis,
            settings,
            message_id,
            stream.EVENT_ERROR,
            {"code": exc.code, "message": exc.message},
        )
    except Exception as exc:  # a detached task must never die silently
        logger.exception("generation_task_failed", error_type=type(exc).__name__)
        await stream.publish(
            redis,
            settings,
            message_id,
            stream.EVENT_ERROR,
            {"code": "internal_error", "message": "Something went wrong on our side."},
        )
    finally:
        await stream.release_generation_lock(redis, message_id)
        await stream.clear_cancellation(redis, message_id)


async def ensure_generation_started(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
) -> None:
    """Start generation unless someone already has.

    The lock is what stops two tabs on the same message from both calling the model. The
    loser simply tails the stream and sees the winner's deltas.
    """
    redis = get_redis()
    if not await stream.acquire_generation_lock(redis, settings, message_id):
        logger.info("generation_already_running", message_id=str(message_id))
        return

    task = asyncio.create_task(
        _generate_detached(settings, user_id=user_id, thread_id=thread_id, message_id=message_id)
    )
    _running.add(task)
    task.add_done_callback(_running.discard)


async def cancel_generation(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
) -> Message:
    """Ask the generator to stop, and close the stream for anyone watching.

    Idempotent, and safe when nothing is generating: a reply that already finished is
    returned as it stands. The flag is what does the work — the generating coroutine may
    be on another replica, so this cannot simply cancel a local task.
    """
    assistant = await _load_assistant_message(db, user_id, thread_id, message_id)
    if assistant.status in ("complete", "cancelled", "failed"):
        return assistant

    redis = get_redis()
    await stream.request_cancellation(redis, settings, message_id)

    if assistant.status == "pending":
        # Nothing has started, so nobody will notice the flag. Close it out here.
        assistant.status = "cancelled"
        assistant.finish_reason = "cancelled"
        await db.commit()
        await stream.publish(
            redis,
            settings,
            message_id,
            stream.EVENT_DONE,
            {"message_id": str(message_id), "finish_reason": "cancelled", "tokens": {}},
        )
        await stream.clear_cancellation(redis, message_id)

    logger.info("cancellation_requested", message_id=str(message_id))
    return assistant


async def regenerate(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
) -> Message:
    """Supersede a reply with a fresh attempt, and return the new pending row.

    The old attempt is marked `cancelled` rather than overwritten. Two reasons: the record
    of what Bella actually said stays intact, and `cancelled` rows are excluded from the
    history sent to the model — so the next attempt is not influenced by the answer it is
    replacing.
    """
    previous = await _load_assistant_message(db, user_id, thread_id, message_id)

    if previous.status in ("pending", "streaming"):
        raise ConflictError(
            "That reply is still being written. Stop it before retrying.",
            code="generation_in_progress",
        )

    thread = (
        await db.execute(
            select(Thread)
            .where(Thread.id == thread_id, Thread.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one()

    next_seq = (
        await db.execute(
            select(func.coalesce(func.max(Message.seq), 0) + 1).where(
                Message.thread_id == thread_id
            )
        )
    ).scalar_one()

    previous.status = "cancelled"
    replacement = Message(
        thread_id=thread_id,
        user_id=user_id,
        seq=next_seq,
        role="assistant",
        content="",
        status="pending",
    )
    db.add(replacement)
    thread.message_count = next_seq
    await db.flush()

    logger.info("regeneration_queued", superseded=str(message_id))
    return replacement


async def replay_terminal_state(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    message_id: uuid.UUID,
) -> Message | None:
    """The completed row, if this message is already finished.

    A stream whose Redis window has expired still has to answer a late reader — the reply
    is in Postgres, which is the durable copy, so it is synthesised back into a `done`.
    """
    message = await _load_assistant_message(db, user_id, thread_id, message_id)
    return message if message.status in ("complete", "cancelled", "failed") else None
