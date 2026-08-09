"""Async engine, session factory, and the RLS-scoped session dependency.

The important piece is `session_for_user`: it sets `app.user_id` as a *transaction-local*
setting, which is what the Row-Level Security policies read. A query that forgets its
`WHERE user_id = ...` then returns zero rows instead of another family member's data.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine

    _engine = create_async_engine(
        settings.sqlalchemy_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,  # survives Cloud SQL dropping idle connections
        pool_recycle=1800,
        connect_args={"server_settings": {"application_name": settings.service_name}},
    )
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised — call init_engine() at startup.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Session factory not initialised — call init_engine() at startup.")
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Unscoped session. Use only for anonymous routes (auth, health)."""
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_for_user(user_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    """Session with RLS scoped to one user for the life of the transaction."""
    async with get_session_factory()() as session:
        try:
            await session.execute(
                text("SELECT set_config('app.user_id', :uid, true)"),
                {"uid": str(user_id)},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database() -> bool:
    """Readiness probe. Cheap, and it fails fast when the pool is exhausted."""
    async with get_session_factory()() as session:
        result = await session.execute(text("SELECT 1"))
        return bool(result.scalar_one() == 1)
