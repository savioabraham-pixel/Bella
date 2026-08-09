"""ASGI application factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    AccessLogMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.redis import close_redis, init_redis
from app.db.session import dispose_engine, init_engine
from app.modules.health.router import router as health_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings
    logger.info(
        "startup",
        service=settings.service_name,
        version=settings.version,
        environment=settings.environment,
    )

    init_engine(settings)
    init_redis(settings)

    if settings.otel_enabled:
        _install_telemetry(app, settings)

    try:
        yield
    finally:
        logger.info("shutdown")
        await close_redis()
        await dispose_engine()


def _install_telemetry(app: FastAPI, settings: Settings) -> None:
    """Wire OpenTelemetry only when it is switched on — the SDK is not free at import."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    from app.db.session import get_engine

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,health/ready")
    SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)
    RedisInstrumentor().instrument()
    logger.info("telemetry_enabled", endpoint=settings.otel_exporter_otlp_endpoint)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="Bella API",
        version=settings.version,
        description="Backend for Bella — a personal AI companion.",
        openapi_url=None if settings.is_production else "/openapi.json",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Middleware is applied bottom-up: the last one added is the outermost. Request
    # context must be outermost so that every log line and every error envelope has an id.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "X-Trace-ID", "Server-Timing"],
        max_age=600,
    )
    if settings.trusted_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
