"""Token budgeting, rolling summarisation, and orphan recovery.

The behaviour under test is what happens to a conversation that outgrows its context
window. The legacy client's answer was to silently drop everything past the last twenty
messages on reload; these assert that the history is compressed rather than lost, and that
nothing is ever sent both verbatim and in summary.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import Settings
from app.modules.threads.prompt import fit_to_budget
from app.providers.base import Turn
from app.providers.tokens import estimate_prompt_tokens, estimate_tokens
from tests.test_auth import bearer
from tests.test_generation import reply_to
from tests.test_me import token_for
from tests.test_threads import make_thread

THREADS_URL = "/v1/threads"


# ── token estimation (unit) ───────────────────────────────────────────────────
@pytest.mark.unit
def test_estimation_scales_with_length() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("hello") >= 1
    assert estimate_tokens("hello " * 100) > estimate_tokens("hello " * 10)


@pytest.mark.unit
def test_indic_script_costs_more_per_character() -> None:
    """A ratio tuned for English under-counts Devanagari and overflows the real window."""
    latin = "namaste how are you today my friend"
    devanagari = "नमस्ते आप कैसे हैं आज मेरे मित्र"

    assert estimate_tokens(devanagari) > estimate_tokens(latin[: len(devanagari)])


@pytest.mark.unit
def test_prompt_estimate_includes_per_turn_overhead() -> None:
    turns = [Turn(role="user", content="a"), Turn(role="assistant", content="b")]
    assert estimate_prompt_tokens("sys", turns) > estimate_tokens("sys") + 2


# ── budget fitting (unit) ─────────────────────────────────────────────────────
@pytest.mark.unit
def test_fitting_keeps_the_newest_turns() -> None:
    turns = [Turn(role="user", content=f"message {index} " + "x" * 400) for index in range(10)]

    kept = fit_to_budget("system", turns, budget_tokens=400)

    assert len(kept) < len(turns)
    assert kept[-1] is turns[-1], "the newest turn must survive"
    assert kept == turns[len(turns) - len(kept) :], "kept turns must stay contiguous"


@pytest.mark.unit
def test_fitting_always_keeps_at_least_the_last_turn() -> None:
    """It is the question being answered. Nothing to reply to is worse than over budget."""
    enormous = [Turn(role="user", content="x" * 100_000)]

    assert fit_to_budget("system", enormous, budget_tokens=10) == enormous


@pytest.mark.unit
def test_fitting_leaves_a_small_history_alone() -> None:
    turns = [Turn(role="user", content="short"), Turn(role="assistant", content="also short")]
    assert fit_to_budget("system", turns, budget_tokens=8000) == turns


# ── summarisation (integration) ───────────────────────────────────────────────
pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
async def test_a_short_conversation_is_not_summarised(
    app_client: AsyncClient, clean_db: None, admin_conn: AsyncConnection
) -> None:
    token = await token_for(app_client, "fb_c1", name="Savio")
    thread_id = await make_thread(app_client, token)

    await reply_to(app_client, token, thread_id, "hello")
    await reply_to(app_client, token, thread_id, "how are you")

    row = (
        (
            await admin_conn.execute(
                text("SELECT summary, summary_upto_seq FROM threads WHERE id = :id"),
                {"id": thread_id},
            )
        )
        .mappings()
        .one()
    )

    assert row["summary"] is None
    assert row["summary_upto_seq"] == 0


@pytest.mark.integration
async def test_a_long_conversation_is_compressed(
    app_client: AsyncClient,
    clean_db: None,
    admin_conn: AsyncConnection,
    settings: Settings,
) -> None:
    """Past the trigger, older turns move into the summary and the watermark advances."""
    token = await token_for(app_client, "fb_c2", name="Savio")
    thread_id = await make_thread(app_client, token)

    # Each turn is deliberately large, so the budget is crossed in a handful of turns
    # rather than needing a hundred round trips.
    bulk = "please remember this detail " * 120
    for index in range(6):
        await reply_to(app_client, token, thread_id, f"item {index}: {bulk}")

    row = (
        (
            await admin_conn.execute(
                text("SELECT summary, summary_upto_seq FROM threads WHERE id = :id"),
                {"id": thread_id},
            )
        )
        .mappings()
        .one()
    )

    assert row["summary"], "a conversation past the trigger must carry a summary"
    assert row["summary_upto_seq"] > 0

    # The mock echoes the transcript it was handed, which is how we can see that real
    # conversation content was compressed rather than an empty string being stored.
    assert "please remember this detail" in row["summary"]
    assert "Savio:" in row["summary"], "the transcript should be attributed by name"

    # Compression is incremental: each pass folds the previous summary in through
    # `previous_summary`, so the stored text covers the most recent compressed span rather
    # than restating the whole thread. The mock cannot fold, so only the span is asserted.
    assert (
        row["summary_upto_seq"]
        < (
            await admin_conn.execute(
                text("SELECT message_count FROM threads WHERE id = :id"), {"id": thread_id}
            )
        ).scalar_one()
    )


@pytest.mark.integration
async def test_summarised_turns_are_not_also_sent_verbatim(
    app_client: AsyncClient,
    clean_db: None,
    admin_conn: AsyncConnection,
) -> None:
    """Sending both makes the model read one exchange twice and treat it as emphasis."""
    token = await token_for(app_client, "fb_c3", name="Savio")
    thread_id = await make_thread(app_client, token)

    bulk = "please remember this detail " * 120
    for index in range(6):
        await reply_to(app_client, token, thread_id, f"item {index}: {bulk}")

    watermark = (
        await admin_conn.execute(
            text("SELECT summary_upto_seq FROM threads WHERE id = :id"), {"id": thread_id}
        )
    ).scalar_one()
    assert watermark > 0

    # The next reply reports how many turns it was given; it must exclude everything at or
    # below the watermark rather than resending the whole history.
    reply = await reply_to(app_client, token, thread_id, "and finally, a short question")
    total = (
        await admin_conn.execute(
            text("SELECT count(*) FROM messages WHERE thread_id = :id AND status = 'complete'"),
            {"id": thread_id},
        )
    ).scalar_one()

    assert "turns so far" in str(reply["content"])
    turns_used = int(str(reply["content"]).split(" turns so far")[0].split()[-1])
    assert turns_used < total


@pytest.mark.integration
async def test_the_conversation_survives_compression(
    app_client: AsyncClient, clean_db: None
) -> None:
    """Every turn is still readable afterwards — compression is for the prompt, not the record."""
    token = await token_for(app_client, "fb_c4", name="Savio")
    thread_id = await make_thread(app_client, token)

    bulk = "please remember this detail " * 120
    for index in range(6):
        await reply_to(app_client, token, thread_id, f"item {index}: {bulk}")

    messages = (
        await app_client.get(
            f"{THREADS_URL}/{thread_id}/messages", headers=bearer(token), params={"limit": 100}
        )
    ).json()["items"]

    assert len(messages) == 12
    assert "item 0" in messages[0]["content"]


# ── orphan recovery (integration) ─────────────────────────────────────────────
@pytest.mark.integration
async def test_orphaned_generations_are_failed_after_the_grace_period(
    app_client: AsyncClient,
    clean_db: None,
    admin_conn: AsyncConnection,
    settings: Settings,
) -> None:
    """A reply whose process died leaves a spinner that never resolves without this."""
    from app.modules.threads.maintenance import fail_orphaned_generations

    token = await token_for(app_client, "fb_c5", name="Savio")
    thread_id = await make_thread(app_client, token)
    await reply_to(app_client, token, thread_id, "hello")

    # Pose the assistant row as a generation abandoned an hour ago.
    await admin_conn.execute(
        text("""
            UPDATE messages
            SET status = 'streaming', created_at = now() - interval '1 hour'
            WHERE thread_id = :id AND role = 'assistant'
        """),
        {"id": thread_id},
    )
    await admin_conn.commit()

    assert await fail_orphaned_generations(settings) >= 1

    row = (
        (
            await admin_conn.execute(
                text("""
                    SELECT status, error FROM messages
                    WHERE thread_id = :id AND role = 'assistant'
                """),
                {"id": thread_id},
            )
        )
        .mappings()
        .one()
    )

    assert row["status"] == "failed"
    assert row["error"]["code"] == "generation_interrupted"


@pytest.mark.integration
async def test_a_generation_still_in_flight_is_left_alone(
    app_client: AsyncClient,
    clean_db: None,
    admin_conn: AsyncConnection,
    settings: Settings,
) -> None:
    """Several replicas boot independently; one must not kill another's live reply."""
    from app.modules.threads.maintenance import fail_orphaned_generations

    token = await token_for(app_client, "fb_c6", name="Savio")
    thread_id = await make_thread(app_client, token)
    await reply_to(app_client, token, thread_id, "hello")

    await admin_conn.execute(
        text("""
            UPDATE messages SET status = 'streaming', created_at = now()
            WHERE thread_id = :id AND role = 'assistant'
        """),
        {"id": thread_id},
    )
    await admin_conn.commit()

    await fail_orphaned_generations(settings)

    status = (
        await admin_conn.execute(
            text("SELECT status FROM messages WHERE thread_id = :id AND role = 'assistant'"),
            {"id": thread_id},
        )
    ).scalar_one()
    assert status == "streaming"
