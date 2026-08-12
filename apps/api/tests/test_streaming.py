"""SSE delivery and resume.

The resume case is the reason the deltas go through Redis at all. A reply that has to be
regenerated because a tunnel dropped is a reply paid for twice, and — for someone reading it
— an answer that changes wording halfway through a sentence.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis

from app.core.redis import get_redis
from app.modules.threads import stream
from tests.test_auth import bearer
from tests.test_generation import parse_sse, stream_reply
from tests.test_me import token_for
from tests.test_threads import make_thread, send

pytestmark = [pytest.mark.integration]

THREADS_URL = "/v1/threads"


def deltas(events: list[tuple[str, dict[str, object]]]) -> str:
    return "".join(str(data.get("text", "")) for event, data in events if event == "delta")


async def test_a_reply_arrives_as_a_sequence_of_deltas(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_sse1", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "tell me about today")

    events = await stream_reply(app_client, token, str(accepted["assistant_message_id"]), thread_id)

    kinds = [event for event, _ in events]
    assert kinds.count("delta") > 1, "a single delta is not a stream"
    assert kinds[-1] == "done"
    assert "tell me about today" in deltas(events)


async def test_the_done_event_carries_usage(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_sse2", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "hello")

    events = await stream_reply(app_client, token, str(accepted["assistant_message_id"]), thread_id)

    event, data = events[-1]
    assert event == "done"
    assert data["message_id"] == str(accepted["assistant_message_id"])
    assert data["finish_reason"] == "stop"
    assert data["tokens"]["in"] > 0
    assert data["tokens"]["out"] > 0


async def test_the_stream_is_not_compressed(app_client: AsyncClient, clean_db: None) -> None:
    """GZip buffers until its minimum size, which would hold the first tokens back."""
    token = await token_for(app_client, "fb_sse3", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "hello")

    response = await app_client.get(
        f"{THREADS_URL}/{thread_id}/stream",
        headers={**bearer(token), "Accept-Encoding": "gzip"},
        params={"message_id": str(accepted["assistant_message_id"])},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("no-cache")
    assert response.headers.get("x-accel-buffering") == "no"


async def test_reconnecting_with_last_event_id_resumes(
    app_client: AsyncClient, clean_db: None
) -> None:
    """The point of mirroring deltas into Redis."""
    token = await token_for(app_client, "fb_sse4", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "a reasonably long question here")
    assistant_id = str(accepted["assistant_message_id"])

    first = await app_client.get(
        f"{THREADS_URL}/{thread_id}/stream",
        headers=bearer(token),
        params={"message_id": assistant_id},
    )
    frames = first.text.split("\n\n")

    # The id of the second delta — as if the connection died right after it.
    ids = [
        line.removeprefix("id: ")
        for block in frames
        for line in block.splitlines()
        if line.startswith("id: ")
    ]
    assert len(ids) > 2
    resume_from = ids[1]

    resumed = await app_client.get(
        f"{THREADS_URL}/{thread_id}/stream",
        headers={**bearer(token), "Last-Event-ID": resume_from},
        params={"message_id": assistant_id},
    )
    assert resumed.status_code == 200

    replayed = parse_sse(resumed.text)
    full = parse_sse(first.text)

    # The resumed stream is a strict suffix: no repetition, nothing missing.
    assert deltas(replayed) == deltas(full)[len(deltas(full)) - len(deltas(replayed)) :]
    assert deltas(replayed) != deltas(full), "resume replayed the whole reply"
    assert replayed[-1][0] == "done"


async def test_a_finished_reply_is_served_from_postgres(
    app_client: AsyncClient, clean_db: None
) -> None:
    """The Redis window expires; the durable copy does not."""
    token = await token_for(app_client, "fb_sse5", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "remember this question")
    assistant_id = str(accepted["assistant_message_id"])

    original = await stream_reply(app_client, token, assistant_id, thread_id)

    # Simulate the stream ageing out of Redis entirely.
    redis: Redis[str] = get_redis()
    await redis.delete(stream.stream_key(uuid.UUID(assistant_id)))

    replayed = await stream_reply(app_client, token, assistant_id, thread_id)

    assert deltas(replayed) == deltas(original)
    assert replayed[-1][0] == "done"


async def test_two_readers_do_not_both_generate(app_client: AsyncClient, clean_db: None) -> None:
    """The generation lock. Two tabs on one message must cost one reply, not two."""
    token = await token_for(app_client, "fb_sse6", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "hello")
    assistant_id = str(accepted["assistant_message_id"])

    first = await stream_reply(app_client, token, assistant_id, thread_id)
    second = await stream_reply(app_client, token, assistant_id, thread_id)

    assert deltas(first) == deltas(second)

    # One assistant row, one answer — not two turns.
    messages = (
        await app_client.get(f"{THREADS_URL}/{thread_id}/messages", headers=bearer(token))
    ).json()["items"]
    assert len(messages) == 2


async def test_a_failed_reply_reports_its_error_on_reconnect(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_sse7", name="Savio")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "__provider_error__")
    assistant_id = str(accepted["assistant_message_id"])

    await stream_reply(app_client, token, assistant_id, thread_id)

    redis: Redis[str] = get_redis()
    await redis.delete(stream.stream_key(uuid.UUID(assistant_id)))

    replayed = await stream_reply(app_client, token, assistant_id, thread_id)
    assert replayed[0][0] == "error"
    assert replayed[0][1]["code"] == "provider_error"


async def test_ownership_is_checked_before_any_bytes_are_sent(
    app_client: AsyncClient, clean_db: None
) -> None:
    """A 404 is only possible before the status line goes out."""
    alice = await token_for(app_client, "fb_ssea", email="ssea@example.test", name="Alice")
    bob = await token_for(app_client, "fb_sseb", email="sseb@example.test", name="Bob")

    thread_id = await make_thread(app_client, alice)
    accepted = await send(app_client, alice, thread_id, "private")

    response = await app_client.get(
        f"{THREADS_URL}/{thread_id}/stream",
        headers=bearer(bob),
        params={"message_id": str(accepted["assistant_message_id"])},
    )
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
