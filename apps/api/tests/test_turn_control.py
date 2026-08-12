"""Stop, retry, summarise on demand, and automatic titles.

The controls a person actually reaches for when a reply is going wrong. Each one has to
leave the transcript honest: stopping keeps the words already written, retrying keeps the
attempt it replaces, and neither may quietly delete anything.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from tests.test_auth import bearer
from tests.test_generation import reply_to, stream_reply
from tests.test_me import token_for
from tests.test_threads import make_thread, send

pytestmark = [pytest.mark.integration]

THREADS_URL = "/v1/threads"


async def messages_of(client: AsyncClient, token: str, thread_id: str) -> list[dict[str, object]]:
    response = await client.get(
        f"{THREADS_URL}/{thread_id}/messages", headers=bearer(token), params={"limit": 100}
    )
    return list(response.json()["items"])


# ── cancel ────────────────────────────────────────────────────────────────────
async def test_cancelling_a_pending_reply_closes_it_out(
    app_client: AsyncClient, clean_db: None
) -> None:
    """Nothing has started yet, so nobody would notice a flag — the row is closed here."""
    token = await token_for(app_client, "fb_tc1", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "a question")

    response = await app_client.post(
        f"{THREADS_URL}/{thread_id}/messages/{accepted['assistant_message_id']}/cancel",
        headers=bearer(token),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    # The user's question is untouched.
    rows = await messages_of(app_client, token, thread_id)
    assert rows[0]["content"] == "a question"
    assert rows[1]["status"] == "cancelled"


async def test_cancelling_is_idempotent(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_tc2", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "a question")
    url = f"{THREADS_URL}/{thread_id}/messages/{accepted['assistant_message_id']}/cancel"

    first = await app_client.post(url, headers=bearer(token))
    second = await app_client.post(url, headers=bearer(token))

    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "cancelled"


async def test_cancelling_a_finished_reply_leaves_it_alone(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_tc3", name="Savio")
    thread_id = await make_thread(app_client, token)
    reply = await reply_to(app_client, token, thread_id, "a question")

    response = await app_client.post(
        f"{THREADS_URL}/{thread_id}/messages/{reply['id']}/cancel", headers=bearer(token)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "complete"
    assert response.json()["content"] == reply["content"]


async def test_a_cancelled_reply_is_not_sent_back_to_the_model(
    app_client: AsyncClient, clean_db: None
) -> None:
    """Only `complete` turns become history; a stopped one must not shape the next reply."""
    token = await token_for(app_client, "fb_tc4", name="Savio")
    thread_id = await make_thread(app_client, token)

    accepted = await send(app_client, token, thread_id, "first question")
    await app_client.post(
        f"{THREADS_URL}/{thread_id}/messages/{accepted['assistant_message_id']}/cancel",
        headers=bearer(token),
    )

    reply = await reply_to(app_client, token, thread_id, "second question")
    # The mock reports how many turns it was handed: both user turns, no cancelled reply.
    assert "2 turns so far" in str(reply["content"])


async def test_one_user_cannot_cancel_anothers_reply(
    app_client: AsyncClient, clean_db: None
) -> None:
    alice = await token_for(app_client, "fb_tca", email="tca@example.test", name="Alice")
    bob = await token_for(app_client, "fb_tcb", email="tcb@example.test", name="Bob")

    thread_id = await make_thread(app_client, alice)
    accepted = await send(app_client, alice, thread_id, "private")

    response = await app_client.post(
        f"{THREADS_URL}/{thread_id}/messages/{accepted['assistant_message_id']}/cancel",
        headers=bearer(bob),
    )
    assert response.status_code == 404


# ── regenerate ────────────────────────────────────────────────────────────────
async def test_regenerating_supersedes_without_deleting(
    app_client: AsyncClient, clean_db: None
) -> None:
    """The record of what Bella actually said stays intact."""
    token = await token_for(app_client, "fb_tr1", name="Savio")
    thread_id = await make_thread(app_client, token)
    first = await reply_to(app_client, token, thread_id, "a question")

    accepted = await app_client.post(
        f"{THREADS_URL}/{thread_id}/messages/{first['id']}/regenerate", headers=bearer(token)
    )
    assert accepted.status_code == 202

    body = accepted.json()
    assert body["assistant_message_id"] != first["id"]
    assert str(body["assistant_message_id"]) in body["stream_url"]

    await stream_reply(app_client, token, str(body["assistant_message_id"]), thread_id)

    rows = await messages_of(app_client, token, thread_id)
    assert [row["role"] for row in rows] == ["user", "assistant", "assistant"]
    assert rows[1]["status"] == "cancelled", "the superseded attempt is kept, not deleted"
    assert rows[1]["content"] == first["content"]
    assert rows[2]["status"] == "complete"


async def test_a_superseded_reply_is_not_sent_back_to_the_model(
    app_client: AsyncClient, clean_db: None
) -> None:
    """The retry must not be influenced by the answer it is replacing."""
    token = await token_for(app_client, "fb_tr2", name="Savio")
    thread_id = await make_thread(app_client, token)
    first = await reply_to(app_client, token, thread_id, "a question")

    body = (
        await app_client.post(
            f"{THREADS_URL}/{thread_id}/messages/{first['id']}/regenerate",
            headers=bearer(token),
        )
    ).json()
    await stream_reply(app_client, token, str(body["assistant_message_id"]), thread_id)

    rows = await messages_of(app_client, token, thread_id)
    # One user turn in, one turn handed to the model — not the superseded reply as well.
    assert "1 turns so far" in str(rows[2]["content"])


async def test_regenerating_a_reply_still_being_written_is_refused(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_tr3", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "a question")

    response = await app_client.post(
        f"{THREADS_URL}/{thread_id}/messages/{accepted['assistant_message_id']}/regenerate",
        headers=bearer(token),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "generation_in_progress"


async def test_a_failed_reply_can_be_regenerated(app_client: AsyncClient, clean_db: None) -> None:
    """The whole point of keeping the turn durable when the provider dies."""
    token = await token_for(app_client, "fb_tr4", name="Savio")
    thread_id = await make_thread(app_client, token)

    accepted = await send(app_client, token, thread_id, "__provider_error__ please")
    await stream_reply(app_client, token, str(accepted["assistant_message_id"]), thread_id)

    body = (
        await app_client.post(
            f"{THREADS_URL}/{thread_id}/messages/{accepted['assistant_message_id']}/regenerate",
            headers=bearer(token),
        )
    ).json()
    assert body["assistant_message_id"] != accepted["assistant_message_id"]

    # The trigger phrase is in the user's own turn, so this attempt fails too — which is
    # the honest thing to assert. What matters is that a failed reply is retryable at all
    # and that each attempt is recorded rather than overwriting the last.
    events = await stream_reply(app_client, token, str(body["assistant_message_id"]), thread_id)
    assert events[-1][0] == "error"

    rows = await messages_of(app_client, token, thread_id)
    assert [row["status"] for row in rows] == ["complete", "cancelled", "failed"]


# ── summarise on demand ───────────────────────────────────────────────────────
async def test_summarise_on_demand_compresses_a_long_thread(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_ts1", name="Savio")
    thread_id = await make_thread(app_client, token)
    for index in range(3):
        await reply_to(app_client, token, thread_id, f"question {index} about the arrangements")

    response = await app_client.post(f"{THREADS_URL}/{thread_id}/summarise", headers=bearer(token))
    assert response.status_code == 200

    body = response.json()
    assert body["summarised"] is True
    assert body["summary"]
    assert body["summary_upto_seq"] > 0


async def test_summarise_declines_on_a_short_thread(
    app_client: AsyncClient, clean_db: None
) -> None:
    """Replacing four turns with a description of them loses more than it saves."""
    token = await token_for(app_client, "fb_ts2", name="Savio")
    thread_id = await make_thread(app_client, token)
    await reply_to(app_client, token, thread_id, "hello")

    body = (
        await app_client.post(f"{THREADS_URL}/{thread_id}/summarise", headers=bearer(token))
    ).json()

    assert body["summarised"] is False
    assert body["summary"] is None
    assert body["summary_upto_seq"] == 0


# ── titles ────────────────────────────────────────────────────────────────────
async def test_a_thread_is_named_by_the_model(
    app_client: AsyncClient, clean_db: None, admin_conn: AsyncConnection
) -> None:
    token = await token_for(app_client, "fb_tt1", name="Savio")
    thread_id = await make_thread(app_client, token)

    await reply_to(app_client, token, thread_id, "what did the lab report say")

    thread = (await app_client.get(f"{THREADS_URL}/{thread_id}", headers=bearer(token))).json()
    assert thread["title_generated"] is True
    assert thread["title"] != "New Conversation"
    # Length is enforced by `_clean_title` rather than trusted from the model — a title is
    # one line in a sidebar, and a model asked for six words will sometimes send thirty.
    assert len(thread["title"]) <= 60
    assert "\n" not in thread["title"]


async def test_a_hand_written_title_survives_the_titler(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_tt2", name="Savio")
    thread_id = await make_thread(app_client, token, "My own title")

    await reply_to(app_client, token, thread_id, "something else entirely")

    thread = (await app_client.get(f"{THREADS_URL}/{thread_id}", headers=bearer(token))).json()
    assert thread["title"] == "My own title"
    assert thread["title_generated"] is False


async def test_the_title_is_only_chosen_once(app_client: AsyncClient, clean_db: None) -> None:
    """A conversation that wanders must not be renamed out from under the reader."""
    token = await token_for(app_client, "fb_tt3", name="Savio")
    thread_id = await make_thread(app_client, token)

    await reply_to(app_client, token, thread_id, "the first subject")
    first_title = (
        await app_client.get(f"{THREADS_URL}/{thread_id}", headers=bearer(token))
    ).json()["title"]

    await reply_to(app_client, token, thread_id, "a completely different subject")
    second_title = (
        await app_client.get(f"{THREADS_URL}/{thread_id}", headers=bearer(token))
    ).json()["title"]

    assert first_title == second_title


async def test_titling_survives_a_provider_failure(
    app_client: AsyncClient, clean_db: None, admin_conn: AsyncConnection
) -> None:
    """A thread with a mediocre title beats a reply that failed because naming it did."""
    token = await token_for(app_client, "fb_tt4", name="Savio")
    thread_id = await make_thread(app_client, token)

    # The reply itself fails, so no title is attempted; the turn must still be intact.
    accepted = await send(app_client, token, thread_id, "__provider_error__ here")
    await stream_reply(app_client, token, str(accepted["assistant_message_id"]), thread_id)

    status = (
        await admin_conn.execute(
            text("SELECT status FROM messages WHERE thread_id = :id AND role = 'assistant'"),
            {"id": thread_id},
        )
    ).scalar_one()
    assert status == "failed"

    thread = (await app_client.get(f"{THREADS_URL}/{thread_id}", headers=bearer(token))).json()
    assert thread["title"] == "New Conversation"
