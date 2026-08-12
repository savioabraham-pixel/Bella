"""Add the auto-title prompt

Titles were derived by truncating the opening question, which reads badly the moment
someone opens with "hi" or pastes three paragraphs. This is the prompt that names a
conversation from its first exchange.

Versioned like the others. A title is short, visible on every row of the sidebar, and the
first thing anyone reads — getting the instruction wrong is very visible, so changing it
should be reviewable and reversible.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Created: 2026-08-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TITLE_TEMPLATE = """\
Name this conversation.

Return a title of at most six words. No quotation marks, no trailing punctuation, no
preamble — the title alone.

Describe the subject, not the interaction: "Lab results for the second marker", never
"A question about lab results" or "User asks about results".

If the exchange is only a greeting with no subject yet, return exactly: New Conversation
"""


def upgrade() -> None:
    op.get_bind().execute(
        sa.text("""
            INSERT INTO prompt_versions (name, version, template, variables, is_active, notes)
            VALUES ('bella.title', 1, :template, CAST('[]' AS jsonb), true, :notes)
        """),
        {
            "template": TITLE_TEMPLATE,
            "notes": "Names a thread from its first exchange.",
        },
    )


def downgrade() -> None:
    op.execute("DELETE FROM prompt_versions WHERE name = 'bella.title'")
