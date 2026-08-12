"""Add the summary prompt, a system prompt that carries it, and the orphan sweep

Three things, all consequences of rolling summarisation landing:

* `bella.summary` — the prompt used to compress older turns. Versioned data like the
  persona, for the same reason: it shapes what Bella remembers, so changing it must be
  reviewable and reversible.
* `bella.system` version 2 — identical to version 1 apart from a CONVERSATION SO FAR
  section. Version 1 is deactivated rather than edited, which is the whole point of
  keeping prompts as versioned rows.
* `fail_orphaned_generations()` — a SECURITY DEFINER sweep. A reply interrupted by a
  process restart leaves its row on `streaming` with nothing left to finish it. The sweep
  crosses users by definition, so it cannot run under RLS as the app role; it is a narrow
  definer function rather than a hole in the policy, exactly like `auth_lookup_user`.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Created: 2026-08-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "bella_app"

SUMMARY_TEMPLATE = """\
You are compressing the earlier part of a conversation so it can be carried forward once
the verbatim transcript no longer fits.

Write a factual summary in the third person, addressed to whoever reads it next. Preserve,
in this order of priority:

1. Anything {{ addressed_name }} asked to be remembered, and any correction they made.
2. Decisions reached, commitments given, and outstanding questions.
3. Facts about their life, work and the people in it that came up in conversation.
4. The emotional register — if they were worried, celebrating, or grieving, say so plainly.

Rules:
- Never invent detail. If something was ambiguous, say it was.
- Keep names exactly as they were used.
- Do not soften or omit difficult subjects; a summary that hides a bereavement causes the
  next reply to blunder into it.
- No preamble, no sign-off. Return the summary alone.
{% if previous_summary %}
Fold this earlier summary into your answer rather than repeating it alongside:

{{ previous_summary }}
{% endif %}"""

SYSTEM_V2_ADDITION = """
{% if conversation_summary -%}
CONVERSATION SO FAR
This is a compressed record of earlier turns in this conversation. Treat it as something
you remember, not as something you were told just now.

{{ conversation_summary }}
{%- endif %}
"""


def upgrade() -> None:
    connection = op.get_bind()

    template_v1 = connection.execute(
        sa.text("SELECT template FROM prompt_versions WHERE name = 'bella.system' AND version = 1")
    ).scalar_one()

    # Version 2 is version 1 with the summary section spliced in before PERSONALITY.
    marker = "PERSONALITY & TONE (STRICT)"
    template_v2 = template_v1.replace(marker, SYSTEM_V2_ADDITION.strip() + "\n\n" + marker, 1)

    connection.execute(
        sa.text("UPDATE prompt_versions SET is_active = false WHERE name = 'bella.system'")
    )
    connection.execute(
        sa.text("""
            INSERT INTO prompt_versions (name, version, template, variables, is_active, notes)
            VALUES ('bella.system', 2, :template, CAST(:variables AS jsonb), true, :notes)
        """),
        {
            "template": template_v2,
            "variables": (
                '["addressed_name","group_name","bio","pronunciation","pronouns",'
                '"local_time","timezone","relationships","sensitive_topics","memories",'
                '"conversation_summary"]'
            ),
            "notes": "Adds CONVERSATION SO FAR for rolling summarisation.",
        },
    )

    connection.execute(
        sa.text("""
            INSERT INTO prompt_versions (name, version, template, variables, is_active, notes)
            VALUES ('bella.summary', 1, :template, CAST(:variables AS jsonb), true, :notes)
        """),
        {
            "template": SUMMARY_TEMPLATE,
            "variables": '["addressed_name","previous_summary"]',
            "notes": "Compresses older turns into threads.summary.",
        },
    )

    op.execute("""
        CREATE OR REPLACE FUNCTION fail_orphaned_generations(p_grace_seconds integer)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            affected integer;
        BEGIN
            UPDATE messages
            SET status = 'failed',
                error = jsonb_build_object(
                    'code', 'generation_interrupted',
                    'message', 'The reply was interrupted and can be retried.'
                )
            WHERE status = 'streaming'
              AND created_at < now() - make_interval(secs => p_grace_seconds);
            GET DIAGNOSTICS affected = ROW_COUNT;
            RETURN affected;
        END
        $$;
    """)
    op.execute(f"GRANT EXECUTE ON FUNCTION fail_orphaned_generations(integer) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS fail_orphaned_generations(integer)")
    op.execute("DELETE FROM prompt_versions WHERE name = 'bella.summary'")
    op.execute("DELETE FROM prompt_versions WHERE name = 'bella.system' AND version = 2")
    op.execute(
        "UPDATE prompt_versions SET is_active = true WHERE name = 'bella.system' AND version = 1"
    )
