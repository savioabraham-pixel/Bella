"""ARQ worker.

Everything that is not required to produce the reply the user is waiting for runs here:
memory extraction and embedding, document parsing, title generation, TTS pre-warm,
transactional email, exports, and the scheduled retention sweeps.

Keeping these off the request path is what makes the chat turn's p95 predictable.
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis, init_redis
from app.db.session import dispose_engine, init_engine

logger = get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings)
    init_engine(settings)
    init_redis(settings)
    ctx["settings"] = settings
    logger.info("worker_startup", environment=settings.environment)


async def shutdown(ctx: dict[str, Any]) -> None:
    await close_redis()
    await dispose_engine()
    logger.info("worker_shutdown")


async def health_probe(ctx: dict[str, Any]) -> str:
    """Trivial task used by the smoke suite to prove the queue round-trips."""
    logger.info("worker_health_probe")
    return "ok"


# ── scheduled jobs ────────────────────────────────────────────────────────────
# Enabled as their modules land. Declared here so the schedule is readable in one place.
# Enabled with `from arq.cron import cron` once the modules below exist.
CRON_JOBS: list[object] = [
    # cron(sweep_expired_memories, hour=3, minute=0),      # decay pass
    # cron(sweep_expired_files, hour=3, minute=15),        # retention
    # cron(process_data_requests, hour={0, 12}, minute=0), # export / erasure SLA
    # cron(rollup_usage, hour=1, minute=0),                # cost per user per day
    # cron(export_to_sheets, hour=2, minute=0),            # owner's legacy workflow
]

FUNCTIONS = [
    health_probe,
]


class WorkerSettings:
    functions = FUNCTIONS
    cron_jobs = CRON_JOBS
    on_startup = startup
    on_shutdown = shutdown

    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))

    max_jobs = 10
    job_timeout = 300  # 5 min — document extraction is the long pole
    keep_result = 3600
    max_tries = 3
    health_check_interval = 30
    # Poison-pill protection: a job that keeps failing must not block the queue forever.
    retry_jobs = True
