"""Conversations: durability, ordering, pagination, idempotency, isolation and search.

The durability cases are the point of the phase. The legacy client held history in
`localStorage`, capped at 20 messages, and wiped it on `pagehide` — so the assertions that
a turn exists on the server the moment it is accepted, and that it survives being read back
by a completely separate request, are the ones that describe the fix.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient

from tests.test_auth import bearer
from tests.test_me import token_for

pytestmark = [pytest.mark.integration, pytest.mark.security]

THREADS_URL = "/v1/threads"
SEARCH_URL = "/v1/search"


async def make_thread(client: AsyncClient, token: str, title: str | None = None) -> str:
    body = {"title": title} if title else {}
    response = await client.post(THREADS_URL, headers=bearer(token), json=body)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def send(
    client: AsyncClient,
    token: str,
    thread_id: str,
    content: str,
    client_message_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"content": content}
    if client_message_id:
        payload["client_message_id"] = client_message_id
    response = await client.post(
        f"{THREADS_URL}/{thread_id}/messages", headers=bearer(token), json=payload
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


# ── threads ───────────────────────────────────────────────────────────────────
async def test_create_and_read_a_thread(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_t1")

    created = await app_client.post(
        THREADS_URL, headers=bearer(token), json={"title": "Lab results"}
    )
    assert created.status_code == 201

    body = created.json()
    assert body["title"] == "Lab results"
    assert body["message_count"] == 0
    assert body["pinned"] is False
    assert body["archived"] is False

    read_back = await app_client.get(f"{THREADS_URL}/{body['id']}", headers=bearer(token))
    assert read_back.status_code == 200
    assert read_back.json()["title"] == "Lab results"


async def test_thread_defaults_its_title(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_t2")
    created = await app_client.post(THREADS_URL, headers=bearer(token), json={})
    assert created.json()["title"] == "New Conversation"


async def test_threads_require_authentication(app_client: AsyncClient) -> None:
    assert (await app_client.get(THREADS_URL)).status_code == 401


async def test_rename_pin_and_archive(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_t3")
    thread_id = await make_thread(app_client, token)

    renamed = await app_client.patch(
        f"{THREADS_URL}/{thread_id}", headers=bearer(token), json={"title": "Renamed"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed"
    # A hand-written title must not be overwritten by the auto-titler later.
    assert renamed.json()["title_generated"] is False

    pinned = await app_client.patch(
        f"{THREADS_URL}/{thread_id}", headers=bearer(token), json={"pinned": True}
    )
    assert pinned.json()["pinned"] is True
    assert pinned.json()["title"] == "Renamed"

    archived = await app_client.patch(
        f"{THREADS_URL}/{thread_id}", headers=bearer(token), json={"archived": True}
    )
    assert archived.json()["archived"] is True

    unarchived = await app_client.patch(
        f"{THREADS_URL}/{thread_id}", headers=bearer(token), json={"archived": False}
    )
    assert unarchived.json()["archived"] is False


async def test_archived_threads_are_hidden_unless_asked_for(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_t4")
    kept = await make_thread(app_client, token, "Kept")
    archived = await make_thread(app_client, token, "Archived")
    await app_client.patch(
        f"{THREADS_URL}/{archived}", headers=bearer(token), json={"archived": True}
    )

    default = (await app_client.get(THREADS_URL, headers=bearer(token))).json()
    assert [row["id"] for row in default["items"]] == [kept]

    inclusive = (
        await app_client.get(THREADS_URL, headers=bearer(token), params={"include_archived": True})
    ).json()
    assert len(inclusive["items"]) == 2


async def test_pinned_threads_sort_first(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_t5")
    first = await make_thread(app_client, token, "First")
    second = await make_thread(app_client, token, "Second")

    # Without pinning, the newer thread leads.
    listed = (await app_client.get(THREADS_URL, headers=bearer(token))).json()["items"]
    assert listed[0]["id"] == second

    await app_client.patch(f"{THREADS_URL}/{first}", headers=bearer(token), json={"pinned": True})

    listed = (await app_client.get(THREADS_URL, headers=bearer(token))).json()["items"]
    assert listed[0]["id"] == first


async def test_delete_removes_the_thread_and_its_messages(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_t6")
    thread_id = await make_thread(app_client, token)
    await send(app_client, token, thread_id, "hello")

    deleted = await app_client.delete(f"{THREADS_URL}/{thread_id}", headers=bearer(token))
    assert deleted.status_code == 204

    gone = await app_client.get(f"{THREADS_URL}/{thread_id}", headers=bearer(token))
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "thread_not_found"


async def test_unknown_thread_is_not_found(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_t7")
    response = await app_client.get(f"{THREADS_URL}/{uuid.uuid4()}", headers=bearer(token))
    assert response.status_code == 404


# ── messages ──────────────────────────────────────────────────────────────────
async def test_a_turn_is_durable_the_moment_it_is_accepted(
    app_client: AsyncClient, clean_db: None
) -> None:
    """The fix for finding H2.

    The user message and a pending assistant row are committed before any model runs, so a
    crash mid-generation leaves a retryable record rather than nothing.
    """
    token = await token_for(app_client, "fb_m1")
    thread_id = await make_thread(app_client, token)

    accepted = await send(app_client, token, thread_id, "What did the lab report say?")
    assert accepted["seq"] == 1
    assert accepted["idempotent_replay"] is False
    assert str(accepted["stream_url"]).startswith(f"/v1/threads/{thread_id}/stream")

    # A completely separate request sees both rows.
    messages = (
        await app_client.get(f"{THREADS_URL}/{thread_id}/messages", headers=bearer(token))
    ).json()["items"]

    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "What did the lab report say?"
    assert messages[0]["status"] == "complete"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["status"] == "pending"
    assert messages[1]["seq"] == 2


async def test_sequence_numbers_advance_across_turns(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_m2")
    thread_id = await make_thread(app_client, token)

    assert (await send(app_client, token, thread_id, "one"))["seq"] == 1
    assert (await send(app_client, token, thread_id, "two"))["seq"] == 3
    assert (await send(app_client, token, thread_id, "three"))["seq"] == 5

    thread = (await app_client.get(f"{THREADS_URL}/{thread_id}", headers=bearer(token))).json()
    assert thread["message_count"] == 6
    assert thread["last_message_at"] is not None


async def test_repeating_a_client_message_id_replays_the_same_turn(
    app_client: AsyncClient, clean_db: None
) -> None:
    """A double-tap on Send must not create two turns, or bill twice."""
    token = await token_for(app_client, "fb_m3")
    thread_id = await make_thread(app_client, token)

    first = await send(app_client, token, thread_id, "hello", client_message_id="c_1")
    second = await send(app_client, token, thread_id, "hello", client_message_id="c_1")

    assert second["message_id"] == first["message_id"]
    assert second["assistant_message_id"] == first["assistant_message_id"]
    assert second["idempotent_replay"] is True

    messages = (
        await app_client.get(f"{THREADS_URL}/{thread_id}/messages", headers=bearer(token))
    ).json()["items"]
    assert len(messages) == 2


async def test_concurrent_sends_get_distinct_sequence_numbers(
    app_client: AsyncClient, clean_db: None
) -> None:
    """The thread row is locked while a seq is allocated, so a race cannot collide."""
    token = await token_for(app_client, "fb_m4")
    thread_id = await make_thread(app_client, token)

    responses = await asyncio.gather(
        *(
            app_client.post(
                f"{THREADS_URL}/{thread_id}/messages",
                headers=bearer(token),
                json={"content": f"message {index}", "client_message_id": f"c_{index}"},
            )
            for index in range(4)
        )
    )

    assert all(response.status_code == 202 for response in responses)
    seqs = sorted(response.json()["seq"] for response in responses)
    assert seqs == [1, 3, 5, 7]


async def test_messages_paginate_oldest_first(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_m5")
    thread_id = await make_thread(app_client, token)
    for index in range(3):
        await send(app_client, token, thread_id, f"message {index}")

    first_page = (
        await app_client.get(
            f"{THREADS_URL}/{thread_id}/messages", headers=bearer(token), params={"limit": 2}
        )
    ).json()
    assert len(first_page["items"]) == 2
    assert first_page["items"][0]["seq"] == 1
    assert first_page["next_cursor"]

    second_page = (
        await app_client.get(
            f"{THREADS_URL}/{thread_id}/messages",
            headers=bearer(token),
            params={"limit": 2, "cursor": first_page["next_cursor"]},
        )
    ).json()
    assert second_page["items"][0]["seq"] == 3

    seen = [row["seq"] for row in first_page["items"] + second_page["items"]]
    assert seen == sorted(seen)
    assert len(seen) == len(set(seen))


async def test_threads_paginate_without_duplicates(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_m6")
    for index in range(5):
        await make_thread(app_client, token, f"Thread {index}")

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, str | int] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await app_client.get(THREADS_URL, headers=bearer(token), params=params)).json()
        seen.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_a_bad_cursor_is_a_client_error(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_m7")
    response = await app_client.get(
        THREADS_URL, headers=bearer(token), params={"cursor": "not-a-cursor"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"


async def test_empty_message_is_rejected(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_m8")
    thread_id = await make_thread(app_client, token)

    response = await app_client.post(
        f"{THREADS_URL}/{thread_id}/messages", headers=bearer(token), json={"content": ""}
    )
    assert response.status_code == 422


async def test_the_accepted_turn_points_at_its_own_reply(
    app_client: AsyncClient, clean_db: None
) -> None:
    """The 202 hands back everything needed to collect the answer later.

    Generation itself is covered in test_generation.py; what matters here is that the
    durable record and the URL that resolves it are produced together.
    """
    token = await token_for(app_client, "fb_m9")
    thread_id = await make_thread(app_client, token)
    accepted = await send(app_client, token, thread_id, "hello")

    assert accepted["assistant_message_id"] != accepted["message_id"]
    assert str(accepted["assistant_message_id"]) in str(accepted["stream_url"])


# ── isolation ─────────────────────────────────────────────────────────────────
async def test_one_user_cannot_see_anothers_threads(
    app_client: AsyncClient, clean_db: None
) -> None:
    alice = await token_for(app_client, "fb_ta", email="ta@example.test")
    bob = await token_for(app_client, "fb_tb", email="tb@example.test")

    await make_thread(app_client, alice, "Alice's conversation")

    bob_list = (await app_client.get(THREADS_URL, headers=bearer(bob))).json()
    assert bob_list["items"] == []


async def test_one_user_cannot_reach_anothers_thread_by_id(
    app_client: AsyncClient, clean_db: None
) -> None:
    """Every route answers 404, never 403 — a 403 would confirm the id is real."""
    alice = await token_for(app_client, "fb_tc", email="tc@example.test")
    bob = await token_for(app_client, "fb_td", email="td@example.test")

    thread_id = await make_thread(app_client, alice, "Private")
    await send(app_client, alice, thread_id, "a private detail")

    attempts = [
        app_client.get(f"{THREADS_URL}/{thread_id}", headers=bearer(bob)),
        app_client.get(f"{THREADS_URL}/{thread_id}/messages", headers=bearer(bob)),
        app_client.patch(
            f"{THREADS_URL}/{thread_id}", headers=bearer(bob), json={"title": "hijacked"}
        ),
        app_client.delete(f"{THREADS_URL}/{thread_id}", headers=bearer(bob)),
        app_client.post(
            f"{THREADS_URL}/{thread_id}/messages", headers=bearer(bob), json={"content": "hi"}
        ),
    ]
    for attempt in attempts:
        response = await attempt
        assert response.status_code == 404, response.text
        assert response.json()["error"]["code"] == "thread_not_found"

    # Alice's conversation is untouched.
    still_there = (
        await app_client.get(f"{THREADS_URL}/{thread_id}/messages", headers=bearer(alice))
    ).json()
    assert still_there["items"][0]["content"] == "a private detail"


# ── search ────────────────────────────────────────────────────────────────────
async def test_search_finds_a_message_across_threads(
    app_client: AsyncClient, clean_db: None
) -> None:
    """Replaces the client-side substring scan, which could only see loaded threads."""
    token = await token_for(app_client, "fb_s1")
    first = await make_thread(app_client, token, "Groceries")
    second = await make_thread(app_client, token, "Travel")
    await send(app_client, token, first, "remember to buy cardamom")
    await send(app_client, token, second, "the flight to Kochi is delayed")

    results = (
        await app_client.get(SEARCH_URL, headers=bearer(token), params={"q": "cardamom"})
    ).json()

    assert results["scope"] == "messages"
    assert len(results["items"]) == 1
    assert results["items"][0]["thread_id"] == first
    assert results["items"][0]["thread_title"] == "Groceries"
    assert "cardamom" in results["items"][0]["snippet"]


async def test_search_can_scope_to_thread_titles(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_s2")
    await make_thread(app_client, token, "Groceries")
    await make_thread(app_client, token, "Travel")

    results = (
        await app_client.get(
            SEARCH_URL, headers=bearer(token), params={"q": "Travel", "scope": "threads"}
        )
    ).json()

    assert len(results["items"]) == 1
    assert results["items"][0]["thread_title"] == "Travel"


async def test_search_skips_pending_assistant_placeholders(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_s3")
    thread_id = await make_thread(app_client, token)
    await send(app_client, token, thread_id, "cardamom")

    results = (
        await app_client.get(SEARCH_URL, headers=bearer(token), params={"q": "cardamom"})
    ).json()
    assert all(row["role"] == "user" for row in results["items"])


async def test_search_returns_nothing_for_an_unmatched_term(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_s4")
    thread_id = await make_thread(app_client, token)
    await send(app_client, token, thread_id, "hello there")

    results = (
        await app_client.get(SEARCH_URL, headers=bearer(token), params={"q": "zzzznothing"})
    ).json()
    assert results["items"] == []


async def test_search_requires_a_query(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_s5")
    response = await app_client.get(SEARCH_URL, headers=bearer(token), params={"q": ""})
    assert response.status_code == 422


async def test_search_cannot_reach_another_users_messages(
    app_client: AsyncClient, clean_db: None
) -> None:
    alice = await token_for(app_client, "fb_sa", email="sa@example.test")
    bob = await token_for(app_client, "fb_sb", email="sb@example.test")

    thread_id = await make_thread(app_client, alice, "Medical")
    await send(app_client, alice, thread_id, "the biopsy result was benign")

    results = (await app_client.get(SEARCH_URL, headers=bearer(bob), params={"q": "biopsy"})).json()
    assert results["items"] == []
