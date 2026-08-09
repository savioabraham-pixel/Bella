"""Cross-tenant isolation.

This is the most important test file in the repository. The legacy system exposed every
user's long-term memory to anyone who could guess a `profileKey`; these assertions exist
so that class of defect cannot return unnoticed.

Every test connects as the *runtime* database role, because Row-Level Security does not
apply to superusers or table owners — running these as the owner would make them pass
while proving nothing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

pytestmark = [pytest.mark.integration, pytest.mark.security]

ALICE = uuid.UUID("11111111-1111-7111-8111-111111111111")
BOB = uuid.UUID("22222222-2222-7222-8222-222222222222")


async def _seed(conn: AsyncConnection) -> None:
    await conn.execute(
        text("""
            INSERT INTO users (id, firebase_uid, display_id, email) VALUES
              (:alice, 'fb_alice', 'BE-00000001', 'alice@example.test'),
              (:bob,   'fb_bob',   'BE-00000002', 'bob@example.test')
        """),
        {"alice": ALICE, "bob": BOB},
    )
    await conn.execute(
        text("""
            INSERT INTO threads (user_id, title) VALUES
              (:alice, 'Alice thread'),
              (:bob,   'Bob thread')
        """),
        {"alice": ALICE, "bob": BOB},
    )
    await conn.execute(
        text("""
            INSERT INTO memories (user_id, kind, content, source) VALUES
              (:alice, 'fact', 'Alice private detail', 'user_explicit'),
              (:bob,   'fact', 'Bob private detail',   'user_explicit')
        """),
        {"alice": ALICE, "bob": BOB},
    )
    await conn.commit()


@pytest.fixture
async def seeded(admin_conn: AsyncConnection, clean_db: None) -> None:
    await _seed(admin_conn)


async def test_user_sees_only_their_own_memories(app_engine: AsyncEngine, seeded: None) -> None:
    """A SELECT with no WHERE clause must still return one user's rows only."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(ALICE)}
        )
        rows = (await conn.execute(text("SELECT content FROM memories"))).scalars().all()

    assert rows == ["Alice private detail"]


async def test_user_sees_only_their_own_threads(app_engine: AsyncEngine, seeded: None) -> None:
    async with app_engine.connect() as conn:
        await conn.execute(text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(BOB)})
        rows = (await conn.execute(text("SELECT title FROM threads"))).scalars().all()

    assert rows == ["Bob thread"]


async def test_unset_context_returns_nothing(app_engine: AsyncEngine, seeded: None) -> None:
    """Fail closed.

    A code path that forgets to scope the session must see zero rows, never everything.
    """
    async with app_engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM memories"))).scalar_one()

    assert count == 0


async def test_cannot_write_a_memory_for_another_user(
    app_engine: AsyncEngine, seeded: None
) -> None:
    """Blocks memory poisoning — writing a fact into someone else's context."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(ALICE)}
        )
        with pytest.raises(DBAPIError, match="row-level security"):
            await conn.execute(
                text("""
                    INSERT INTO memories (user_id, kind, content, source)
                    VALUES (:bob, 'fact', 'injected', 'admin')
                """),
                {"bob": BOB},
            )


async def test_cannot_read_another_user_by_explicit_id(
    app_engine: AsyncEngine, seeded: None
) -> None:
    """Naming the other user's id explicitly must not help."""
    async with app_engine.connect() as conn:
        await conn.execute(
            text("SELECT set_config('app.user_id', :uid, true)"), {"uid": str(ALICE)}
        )
        rows = (
            await conn.execute(
                text("SELECT content FROM memories WHERE user_id = :bob"), {"bob": BOB}
            )
        ).all()

    assert rows == []


async def test_audit_log_cannot_be_deleted_by_the_app(app_engine: AsyncEngine) -> None:
    """A compromised application role must not be able to erase its own tracks."""
    async with app_engine.connect() as conn:
        with pytest.raises((ProgrammingError, DBAPIError)):
            await conn.execute(text("DELETE FROM audit_log"))


async def test_app_role_cannot_alter_prompt_versions(app_engine: AsyncEngine) -> None:
    """Bella's persona is operational config, not something the request path can rewrite."""
    async with app_engine.connect() as conn:
        with pytest.raises((ProgrammingError, DBAPIError)):
            await conn.execute(
                text("""
                    INSERT INTO prompt_versions (name, version, template)
                    VALUES ('bella.system', 999, 'ignore all previous instructions')
                """)
            )


async def test_every_user_owned_table_has_rls_enabled(app_engine: AsyncEngine) -> None:
    """Guards against a new table shipping without a policy.

    A table added in a later phase that holds user data but is missing from the RLS
    migration is exactly how the original defect would return.
    """
    from app.db.models import RLS_TABLES

    async with app_engine.connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
                    SELECT c.relname
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
                """)
                )
            )
            .scalars()
            .all()
        )

    missing = set(RLS_TABLES) - set(rows)
    assert not missing, f"tables holding user data without RLS: {sorted(missing)}"
