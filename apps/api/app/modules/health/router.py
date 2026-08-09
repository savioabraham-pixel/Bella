"""Liveness and readiness.

`/health`        — is the process up? Never touches a dependency, so a slow database
                   cannot cause the orchestrator to kill a healthy container.
`/health/ready`  — can this instance serve traffic? Checks every hard dependency.
"""

from __future__ import annotations

import asyncio
import time
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis import check_redis
from app.db.session import check_database

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

_STARTED_AT = time.monotonic()
_CHECK_TIMEOUT_SECONDS = 3.0


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str
    uptime_seconds: float


class DependencyStatus(BaseModel):
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    dependencies: dict[str, DependencyStatus] = Field(default_factory=dict)


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        service=settings.service_name,
        version=settings.version,
        environment=settings.environment,
        uptime_seconds=round(time.monotonic() - _STARTED_AT, 3),
    )


async def _probe(name: str, check: object) -> tuple[str, DependencyStatus]:
    started = time.perf_counter()
    try:
        healthy = await asyncio.wait_for(check(), timeout=_CHECK_TIMEOUT_SECONDS)  # type: ignore[operator]
        return name, DependencyStatus(
            healthy=bool(healthy),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except TimeoutError:
        return name, DependencyStatus(healthy=False, error="timeout")
    except Exception as exc:  # a readiness probe must never raise
        logger.warning("readiness_check_failed", dependency=name, error_type=type(exc).__name__)
        return name, DependencyStatus(healthy=False, error=type(exc).__name__)


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(response: Response) -> ReadinessResponse:
    results = await asyncio.gather(
        _probe("database", check_database),
        _probe("redis", check_redis),
    )
    dependencies = dict(results)
    ready = all(dep.healthy for dep in dependencies.values())

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if ready else "degraded",
        dependencies=dependencies,
    )
