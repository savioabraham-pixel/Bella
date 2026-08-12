"""Server-side prompt assembly.

This module is the end of `buildSystemPrompt()`. In the legacy client the browser composed
the entire instruction — persona, nineteen hardcoded biographies, and the conversation
history — and posted it to a backend that accepted whatever it was given. Anyone could edit
the prompt in devtools, and everyone's biography shipped to everyone's browser.

Here the persona comes from `prompt_versions`, the personal detail comes from the caller's
own rows under RLS, and the client contributes exactly one thing: the text of the message
it just sent.

Templates render in Jinja2's **sandboxed** environment. A prompt is data edited through the
admin surface, and data must not be able to reach into Python objects.
"""

from __future__ import annotations

import datetime as dt
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.models.conversations import Message
from app.db.models.identity import Profile, Relationship, User
from app.db.models.memory import Memory
from app.db.models.system import PromptVersion
from app.providers.base import Turn
from app.providers.tokens import estimate_tokens, estimate_turn_tokens

logger = get_logger(__name__)

PROMPT_NAME = "bella.system"

# Memories are ranked and retrieved properly in Phase 4. Until then the most recent handful
# is a better answer than none, and far better than the legacy behaviour of concatenating
# every memory the user had into the prompt on every single turn.
_MEMORY_LIMIT = 20

_environment = SandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, autoescape=False)


class PromptUnavailableError(AppError):
    status_code = 503
    code = "prompt_unavailable"
    message = "Bella is not configured to reply yet."


async def active_template(db: AsyncSession, name: str = PROMPT_NAME) -> PromptVersion:
    version = (
        await db.execute(
            select(PromptVersion).where(
                PromptVersion.name == name,
                PromptVersion.is_active.is_(True),
                PromptVersion.cohort.is_(None),
            )
        )
    ).scalar_one_or_none()
    if version is None:
        logger.error("no_active_prompt_version", name=name)
        raise PromptUnavailableError
    return version


def render(template: str, **variables: object) -> str:
    try:
        return _environment.from_string(template).render(**variables).strip()
    except TemplateError as exc:
        logger.error("prompt_render_failed", error_type=type(exc).__name__)
        raise PromptUnavailableError from exc


def _local_time(timezone: str) -> str:
    """The legacy client computed this in the browser and got DST wrong twice a year."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return dt.datetime.now(tz=zone).strftime("%A %d %B %Y, %H:%M")


async def build_system_prompt(
    db: AsyncSession,
    user: User,
    profile: Profile | None,
    *,
    conversation_summary: str | None = None,
) -> tuple[str, uuid.UUID]:
    """Render the system prompt for one person. Returns `(prompt, prompt_version_id)`."""
    version = await active_template(db)

    relationships = (
        (
            await db.execute(
                select(Relationship)
                .where(Relationship.user_id == user.id, Relationship.ask_about.is_(True))
                .order_by(Relationship.created_at)
            )
        )
        .scalars()
        .all()
    )

    memories = (
        (
            await db.execute(
                select(Memory.content)
                .where(Memory.user_id == user.id)
                .order_by(Memory.created_at.desc())
                .limit(_MEMORY_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    addressed_name = (profile.preferred_name if profile else None) or "there"

    rendered = render(
        version.template,
        addressed_name=addressed_name,
        group_name=profile.group_name if profile else None,
        bio=profile.bio if profile else None,
        pronunciation=profile.pronunciation if profile else None,
        pronouns=profile.pronouns if profile else None,
        local_time=_local_time(user.timezone),
        timezone=user.timezone,
        relationships=[
            {"name": row.name, "relation": row.relation, "notes": row.notes}
            for row in relationships
        ],
        # Names only. What makes a topic sensitive — a bereavement, an illness — is
        # exactly what must not be restated back into the prompt.
        sensitive_topics=[row.name for row in relationships if row.sensitivity == "high"],
        memories=list(memories),
        conversation_summary=conversation_summary,
    )

    return rendered, version.id


async def load_history(
    db: AsyncSession,
    settings: Settings,
    *,
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    upto_seq: int,
    after_seq: int = 0,
) -> list[Message]:
    """Complete, non-empty turns in `(after_seq, upto_seq]`, oldest first.

    `after_seq` is the summarisation watermark: turns at or below it are represented by
    `threads.summary` and must not also appear verbatim, or the model reads the same
    exchange twice and treats it as emphasis.
    """
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.thread_id == thread_id,
                    Message.user_id == user_id,
                    Message.seq <= upto_seq,
                    Message.seq > after_seq,
                    Message.status == "complete",
                    Message.role.in_(("user", "assistant")),
                    Message.content != "",
                )
                .order_by(Message.seq.desc())
                .limit(settings.llm_history_turns)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


def to_turns(messages: list[Message]) -> list[Turn]:
    return [
        Turn(role="user" if row.role == "user" else "assistant", content=row.content)
        for row in messages
    ]


def fit_to_budget(system: str, turns: list[Turn], budget_tokens: int) -> list[Turn]:
    """Drop the oldest turns until the prompt fits, keeping the newest.

    The last turn is always kept even when it alone exceeds the budget — it is the question
    being answered, and returning nothing to reply to is worse than a prompt over budget
    that the vendor will truncate itself.
    """
    if not turns:
        return turns

    available = budget_tokens - estimate_tokens(system)
    kept: list[Turn] = []
    used = 0

    for turn in reversed(turns):
        cost = estimate_turn_tokens(turn)
        if kept and used + cost > available:
            break
        kept.append(turn)
        used += cost

    if len(kept) < len(turns):
        logger.info("history_trimmed", kept=len(kept), dropped=len(turns) - len(kept))

    return list(reversed(kept))


async def build_turns(
    db: AsyncSession,
    settings: Settings,
    *,
    thread_id: uuid.UUID,
    user_id: uuid.UUID,
    upto_seq: int,
    after_seq: int = 0,
) -> list[Turn]:
    """The verbatim window, oldest first, unbudgeted.

    Kept for callers that do not assemble a system prompt of their own — the summariser is
    the one that matters.
    """
    messages = await load_history(
        db,
        settings,
        thread_id=thread_id,
        user_id=user_id,
        upto_seq=upto_seq,
        after_seq=after_seq,
    )
    return to_turns(messages)
