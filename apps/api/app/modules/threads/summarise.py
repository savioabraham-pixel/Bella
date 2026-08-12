"""Rolling summarisation.

A conversation that outgrows the context budget has to lose something. The choice is
between dropping the oldest turns — which is what the legacy client did, silently, on every
reload — and compressing them into a summary that travels with the thread.

Timing matters as much as the compression. This runs **after** a reply has been delivered,
not before, and it triggers at 70% of budget rather than at 100%. Both are the same
decision: the turn that crosses the line should not be the turn that pays for the summary.
By the time the budget is genuinely tight, the summary is already there.

`threads.summary_upto_seq` is the watermark. Turns at or below it are represented by the
summary and are not also sent verbatim — sending both makes the model read one exchange
twice and treat the repetition as emphasis.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models.conversations import Message, Thread
from app.db.models.identity import Profile
from app.modules.threads.prompt import active_template, load_history, render, to_turns
from app.providers.base import GenerationRequest, Turn
from app.providers.registry import generate as call_provider
from app.providers.tokens import estimate_prompt_tokens, estimate_turn_tokens

logger = get_logger(__name__)

SUMMARY_PROMPT_NAME = "bella.summary"

# Never compress the most recent exchanges, however large they are. The turn just answered
# and the one before it are what "what were we just saying" means.
_ALWAYS_VERBATIM_TURNS = 4


def _budget(settings: Settings, ratio: float) -> int:
    return int(settings.llm_context_budget_tokens * ratio)


def _split_at_target(
    messages: list[Message], settings: Settings
) -> tuple[list[Message], list[Message]]:
    """Split into `(to_compress, to_keep_verbatim)`.

    Walks backwards accumulating the tail until the target is reached; everything older is
    the compression candidate.
    """
    target = _budget(settings, settings.llm_summary_target_ratio)
    keep: list[Message] = []
    used = 0

    for message in reversed(messages):
        cost = estimate_turn_tokens(Turn(role="user", content=message.content))
        if len(keep) >= _ALWAYS_VERBATIM_TURNS and used + cost > target:
            break
        keep.append(message)
        used += cost

    keep.reverse()
    compress = messages[: len(messages) - len(keep)]
    return compress, keep


def _transcript(messages: list[Message], addressed_name: str) -> str:
    lines = []
    for message in messages:
        speaker = addressed_name if message.role == "user" else "Bella"
        lines.append(f"{speaker}: {message.content}")
    return "\n\n".join(lines)


async def maybe_summarise(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    thread_id: uuid.UUID,
    force: bool = False,
) -> bool:
    """Compress older turns if the verbatim tail has grown past the trigger.

    Returns whether anything was written. Failures are swallowed: a summary that could not
    be produced is a degraded next turn, not a reason to fail the reply that just
    succeeded.
    """
    thread = (await db.execute(select(Thread).where(Thread.id == thread_id))).scalar_one_or_none()
    if thread is None:  # pragma: no cover — caller just generated into it
        return False

    messages = await load_history(
        db,
        settings,
        thread_id=thread_id,
        user_id=user_id,
        upto_seq=thread.message_count + 1,
        after_seq=thread.summary_upto_seq,
    )
    # `force` still respects the verbatim floor. Compressing a four-turn conversation on
    # request would replace the actual words with a description of them for no gain.
    if len(messages) <= _ALWAYS_VERBATIM_TURNS:
        return False

    turns = to_turns(messages)
    over_trigger = estimate_prompt_tokens("", turns) >= _budget(
        settings, settings.llm_summary_trigger_ratio
    )
    if not (force or over_trigger):
        return False

    compress, _keep = _split_at_target(messages, settings)
    if force and not compress:
        # Under the target, so the split kept everything. Compress all but the floor.
        compress = messages[:-_ALWAYS_VERBATIM_TURNS]
    if not compress:
        return False

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    addressed_name = (profile.preferred_name if profile else None) or "They"

    version = await active_template(db, SUMMARY_PROMPT_NAME)
    system = render(
        version.template,
        addressed_name=addressed_name,
        previous_summary=thread.summary,
    )

    request = GenerationRequest(
        system=system,
        turns=[Turn(role="user", content=_transcript(compress, addressed_name))],
        model=settings.llm_model,
        max_output_tokens=settings.llm_summary_max_output_tokens,
        # Low temperature: this is compression, not composition.
        temperature=0.2,
    )

    try:
        result = await call_provider(settings, request)
    except AppError as exc:
        logger.warning("summarisation_failed", code=exc.code)
        return False

    if not result.text.strip():
        logger.warning("summarisation_empty")
        return False

    thread.summary = result.text.strip()
    thread.summary_upto_seq = compress[-1].seq
    await db.flush()

    logger.info(
        "thread_summarised",
        compressed_turns=len(compress),
        upto_seq=thread.summary_upto_seq,
        output_tokens=result.output_tokens,
    )
    return True
