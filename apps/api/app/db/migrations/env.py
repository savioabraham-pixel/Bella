"""Alembic environment.

The database URL comes from Settings, so there is exactly one place that knows how to
reach the database and no credential is ever written into a config file.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any, Literal

import pgvector.sqlalchemy
from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().alembic_url)

target_metadata = Base.metadata


def include_object(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    """Keep autogenerate away from things it does not own.

    pgvector's HNSW indexes and the RLS policies are declared in hand-written migrations;
    autogenerate would otherwise try to drop them on every run.
    """
    return not (type_ == "table" and name in {"spatial_ref_sys", "alembic_version"})


def render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render pgvector columns with the import they need.

    Alembic renders unknown types by their repr, which for `Vector` produces a
    `pgvector.sqlalchemy...` reference that the generated module never imports. Emitting
    the import here — rather than putting it unconditionally in script.py.mako — keeps
    migrations that use no vector columns free of an unused import.
    """
    if type_ == "type" and isinstance(obj, pgvector.sqlalchemy.Vector):
        autogen_context.imports.add("import pgvector.sqlalchemy")
        return f"pgvector.sqlalchemy.Vector(dim={obj.dim})"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        render_item=render_item,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        render_item=render_item,
        compare_type=True,
        compare_server_default=True,
        # Every migration runs in a transaction: a failure leaves no half-applied schema.
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
