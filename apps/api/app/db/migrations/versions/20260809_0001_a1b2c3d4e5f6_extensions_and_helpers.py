"""Extensions and helper functions

Runs before any table exists. Everything here is idempotent so a re-run against a
partially provisioned database is safe.

Revision ID: a1b2c3d4e5f6
Revises:
Created: 2026-08-09

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# UUIDv7: time-ordered, so primary keys cluster on insert and index locality stays good.
# Postgres 18 ships uuidv7() natively; on 16/17 we define a compatible function. The
# CREATE OR REPLACE is skipped when the built-in already exists.
UUIDV7_FUNCTION = """
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc
        WHERE proname = 'uuidv7' AND pronamespace = 'pg_catalog'::regnamespace
    ) THEN
        CREATE OR REPLACE FUNCTION public.uuidv7() RETURNS uuid
        AS $fn$
            SELECT encode(
                set_bit(
                    set_bit(
                        overlay(
                            uuid_send(gen_random_uuid())
                            PLACING substring(
                                int8send(
                                    floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint
                                ) FROM 3
                            )
                            FROM 1 FOR 6
                        ),
                        52, 1
                    ),
                    53, 1
                ),
                'hex'
            )::uuid;
        $fn$ LANGUAGE sql VOLATILE;
    END IF;
END
$do$;
"""


def upgrade() -> None:
    # pgcrypto  — gen_random_bytes / gen_random_uuid, used by uuidv7() and token hashing
    # vector    — pgvector: semantic memory and document-chunk retrieval
    # citext    — case-insensitive email without a functional index everywhere
    # pg_trgm   — trigram search for fuzzy name and thread-title matching
    # unaccent  — folds diacritics so search works across the four supported languages
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute(UUIDV7_FUNCTION)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.uuidv7()")
    # Extensions are intentionally left in place: other schemas in the same database may
    # depend on them, and dropping `vector` would silently destroy every embedding column.
