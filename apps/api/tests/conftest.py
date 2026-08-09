"""Shared fixtures.

Integration tests run against the real Postgres and Redis from compose, using the
separate `bella_test` database created by the postgres init scripts. Nothing here ever
touches the development database.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.core.config import Settings, get_settings
from app.core.redis import close_redis, init_redis
from app.db.session import dispose_engine, init_engine
from app.main import create_app


@pytest.fixture(scope="session")
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def app_client(settings: Settings) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client bound to the ASGI app — no network, no port binding."""
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # `create_app` does not run lifespan under ASGITransport, so wire the
        # dependencies the routes need explicitly.
        init_engine(settings)
        init_redis(settings)
        try:
            yield client
        finally:
            await close_redis()
            await dispose_engine()


@pytest.fixture
async def app_engine(settings: Settings) -> AsyncGenerator[AsyncEngine, None]:
    """Engine connected as the *runtime* role, so RLS applies exactly as in production.

    Function-scoped with NullPool on purpose: pytest-asyncio gives each test its own
    event loop, and a pooled connection created on one loop cannot be reused on another
    ("attached to a different loop"). Pooling buys nothing in a test anyway.
    """
    engine = create_async_engine(settings.sqlalchemy_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def admin_conn(settings: Settings) -> AsyncGenerator[AsyncConnection, None]:
    """Connection as the migrator role — bypasses RLS, used to arrange fixtures."""
    url = os.environ.get("TEST_ADMIN_DATABASE_URL") or settings.sqlalchemy_url.replace(
        "bella_app:bella_app_local", "bella_migrator:bella_migrator_local"
    )
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as conn:
        yield conn
        await conn.rollback()
    await engine.dispose()


@pytest.fixture
async def clean_db(admin_conn: AsyncConnection) -> AsyncGenerator[None, None]:
    """Truncate between tests.

    TRUNCATE rather than DELETE: the audit_log carries an append-only trigger that
    rejects row deletes by design, and TRUNCATE does not fire row-level triggers.
    """
    await admin_conn.execute(text("TRUNCATE users, audit_log, feature_flags CASCADE"))
    await admin_conn.commit()
    yield
