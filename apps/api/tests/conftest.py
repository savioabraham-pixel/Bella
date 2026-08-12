"""Shared fixtures.

Integration tests run against the real Postgres and Redis from compose, using the
separate `bella_test` database created by the postgres init scripts. Nothing here ever
touches the development database.
"""

from __future__ import annotations

import json
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

# Stand-in for the migration-seeded persona, used only when the real seed is missing. It
# exercises the same render path — a variable and a conditional — without duplicating the
# production template, which stays the migration's business.
_FALLBACK_PROMPT = """\
You are Bella, speaking with {{ addressed_name }}.
{%- if bio %}
Background: {{ bio }}
{%- endif %}
"""


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
    """Truncate between tests, preserving seeded reference data.

    TRUNCATE rather than DELETE: the audit_log carries an append-only trigger that
    rejects row deletes by design, and TRUNCATE does not fire row-level triggers.

    `prompt_versions` has to be carried across by hand. It references `users` through
    `created_by`, and TRUNCATE ... CASCADE empties every table with a foreign key to the
    target regardless of that key's ON DELETE action — so truncating users silently takes
    Bella's persona with it, and every generation afterwards fails with `prompt_unavailable`.
    Snapshotting inside the same transaction keeps the migration as the single source of the
    seed rather than duplicating the template into the test suite.
    """
    prompts = (
        (
            await admin_conn.execute(
                text("""
                    SELECT id, name, version, template, variables, is_active, cohort, notes
                    FROM prompt_versions
                """)
            )
        )
        .mappings()
        .all()
    )

    await admin_conn.execute(text("TRUNCATE users, audit_log, feature_flags CASCADE"))

    for prompt in prompts:
        row = dict(prompt)
        # JSONB comes back decoded; the driver needs text plus an explicit cast to put it
        # back, not the Python list it just handed us.
        row["variables"] = json.dumps(row["variables"])
        await admin_conn.execute(
            text("""
                INSERT INTO prompt_versions
                    (id, name, version, template, variables, is_active, cohort, notes)
                VALUES
                    (:id, :name, :version, :template, CAST(:variables AS jsonb),
                     :is_active, :cohort, :notes)
            """),
            row,
        )

    if not prompts:
        # Self-healing. A database whose seed was destroyed before this fixture existed
        # would otherwise stay broken for good, and every generation test would fail with
        # `prompt_unavailable` for a reason that has nothing to do with the code.
        await admin_conn.execute(
            text("""
                INSERT INTO prompt_versions (name, version, template, variables, is_active, notes)
                VALUES ('bella.system', 1, :template, '[]'::jsonb, true, :notes)
            """),
            {"template": _FALLBACK_PROMPT, "notes": "restored by the test suite"},
        )

    await admin_conn.commit()
    yield
