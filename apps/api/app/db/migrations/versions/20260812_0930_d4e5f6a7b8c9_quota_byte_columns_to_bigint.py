"""Widen the storage quota columns to bigint

`user_quotas.storage_limit_bytes` defaults to 2 GiB — 2147483648 — which is exactly one
past the int4 ceiling of 2147483647. The column was created as INTEGER, so inserting a
quota row with its default raised:

    OverflowError: value out of int32 range

Every first-time sign-in creates a quota row, so this made user provisioning impossible.
It surfaced the moment the auth module first inserted one; nothing had written to the table
before that.

`storage_bytes_used` is widened alongside it: a used-bytes counter that cannot represent
more than its own limit is the same bug waiting on a larger plan.

Revision ID: d4e5f6a7b8c9
Revises: c7d8e9f0a1b2
Created: 2026-08-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("storage_bytes_used", "storage_limit_bytes")


def upgrade() -> None:
    for column in COLUMNS:
        op.alter_column(
            "user_quotas",
            column,
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Lossy by nature: any row above the int4 ceiling cannot survive the narrowing, which
    # is the whole reason for the widening. Postgres will refuse rather than truncate.
    for column in COLUMNS:
        op.alter_column(
            "user_quotas",
            column,
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
