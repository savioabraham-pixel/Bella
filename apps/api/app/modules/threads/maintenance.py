"""Recovery for generations their process did not survive.

Generation runs in the API process, detached from the request that started it — which is
what lets a reply outlive a dropped connection, and also what leaves a row on `streaming`
with nothing to finish it when the process itself goes away mid-reply.

The turn is never lost: the user's message and the assistant row are both committed before
the model is called. What the sweep does is move the abandoned row to `failed`, so the UI
shows a retry instead of a spinner that never resolves.

It crosses users by definition, so it runs through a SECURITY DEFINER function rather than
under RLS as the app role.
"""

from __future__ import annotations

from sqlalchemy import text

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import get_session_factory

logger = get_logger(__name__)


async def fail_orphaned_generations(settings: Settings) -> int:
    """Mark stale `streaming` rows as failed. Returns how many.

    The grace period is what makes this safe to run on every boot with several replicas
    live: a reply still in progress elsewhere is younger than the grace period, so a
    starting replica cannot kill a running one's work.
    """
    async with get_session_factory()() as session:
        affected = (
            await session.execute(
                text("SELECT fail_orphaned_generations(:grace)"),
                {"grace": settings.orphaned_generation_grace_seconds},
            )
        ).scalar_one()
        await session.commit()

    if affected:
        logger.warning("orphaned_generations_failed", count=affected)
    return int(affected)
