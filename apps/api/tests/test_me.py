"""Profile, preferences, relationships, quotas and consent.

The isolation cases matter most. `relationships` is where the hardcoded FOLLOWUP map lands
and `profiles` is where the nineteen biographies land, so "can Alice read or write Bob's
row" is the same question the legacy `profileKey` answered wrongly.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.test_auth import bearer, sign_in

pytestmark = [pytest.mark.integration, pytest.mark.security]

ME_URL = "/v1/me"
PREFERENCES_URL = "/v1/me/preferences"
RELATIONSHIPS_URL = "/v1/me/relationships"
QUOTAS_URL = "/v1/me/quotas"
CONSENTS_URL = "/v1/me/consents"


async def token_for(client: AsyncClient, uid: str, **kwargs: str) -> str:
    return str((await sign_in(client, uid, **kwargs)).json()["access_token"])


# ── profile ───────────────────────────────────────────────────────────────────
async def test_read_profile(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_me", email="me@example.test", name="Savio")

    response = await app_client.get(ME_URL, headers=bearer(token))
    assert response.status_code == 200

    body = response.json()
    assert body["preferred_name"] == "Savio"
    assert body["email"] == "me@example.test"
    assert body["timezone"] == "Asia/Kolkata"
    assert body["role"] == "member"
    assert body["onboarding_complete"] is True


async def test_profile_requires_authentication(app_client: AsyncClient) -> None:
    assert (await app_client.get(ME_URL)).status_code == 401


async def test_patch_updates_only_supplied_fields(app_client: AsyncClient, clean_db: None) -> None:
    """The distinction the legacy client never made: absent means "leave alone"."""
    token = await token_for(app_client, "fb_patch", name="Savio")

    first = await app_client.patch(
        ME_URL, headers=bearer(token), json={"pronunciation": "SAV-ee-oh", "pronouns": "he/him"}
    )
    assert first.status_code == 200
    assert first.json()["pronunciation"] == "SAV-ee-oh"

    # A second PATCH touching one field must not clear the others.
    second = await app_client.patch(ME_URL, headers=bearer(token), json={"gender": "male"})
    assert second.status_code == 200

    body = second.json()
    assert body["gender"] == "male"
    assert body["pronunciation"] == "SAV-ee-oh"
    assert body["pronouns"] == "he/him"
    assert body["preferred_name"] == "Savio"


async def test_patch_with_explicit_null_clears_a_field(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_clear", name="Savio")

    await app_client.patch(ME_URL, headers=bearer(token), json={"pronunciation": "SAV-ee-oh"})
    response = await app_client.patch(ME_URL, headers=bearer(token), json={"pronunciation": None})

    assert response.status_code == 200
    assert response.json()["pronunciation"] is None


async def test_patch_writes_locale_and_timezone_to_the_user_row(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_tz")

    response = await app_client.patch(
        ME_URL, headers=bearer(token), json={"timezone": "Europe/London", "locale": "en-GB"}
    )
    assert response.status_code == 200
    assert response.json()["timezone"] == "Europe/London"
    assert response.json()["locale"] == "en-GB"


async def test_patch_rejects_an_unknown_timezone(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_badtz")

    response = await app_client.patch(
        ME_URL, headers=bearer(token), json={"timezone": "Mars/Olympus_Mons"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_patch_rejects_unknown_fields(app_client: AsyncClient, clean_db: None) -> None:
    """`extra="forbid"` — a typo must fail loudly rather than be silently dropped."""
    token = await token_for(app_client, "fb_extra")

    response = await app_client.patch(
        ME_URL, headers=bearer(token), json={"role": "owner", "status": "active"}
    )
    assert response.status_code == 422


async def test_patch_rejects_a_future_date_of_birth(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_dob")

    response = await app_client.patch(
        ME_URL, headers=bearer(token), json={"date_of_birth": "2099-01-01"}
    )
    assert response.status_code == 422


async def test_one_user_cannot_read_anothers_profile(
    app_client: AsyncClient, clean_db: None
) -> None:
    """There is no parameter to try: the subject comes from the token.

    This is the structural replacement for `profileKey`, which took the identity from the
    request body and returned whatever was asked for.
    """
    alice_token = await token_for(app_client, "fb_a", email="a@example.test", name="Alice")
    bob_token = await token_for(app_client, "fb_b", email="b@example.test", name="Bob")

    alice = (await app_client.get(ME_URL, headers=bearer(alice_token))).json()
    bob = (await app_client.get(ME_URL, headers=bearer(bob_token))).json()

    assert alice["preferred_name"] == "Alice"
    assert bob["preferred_name"] == "Bob"
    assert alice["id"] != bob["id"]


# ── preferences ───────────────────────────────────────────────────────────────
async def test_preferences_start_at_their_defaults(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_prefs")

    body = (await app_client.get(PREFERENCES_URL, headers=bearer(token))).json()
    assert body["theme"] == "system"
    assert body["voice_replies"] is True
    assert body["notifications_enabled"] is True
    assert body["location_enabled"] is False
    assert body["message_retention_days"] is None


async def test_preferences_round_trip(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_prefs2")

    patched = await app_client.patch(
        PREFERENCES_URL,
        headers=bearer(token),
        json={"theme": "dark", "clock_24h": True, "message_retention_days": 90},
    )
    assert patched.status_code == 200

    reread = (await app_client.get(PREFERENCES_URL, headers=bearer(token))).json()
    assert reread["theme"] == "dark"
    assert reread["clock_24h"] is True
    assert reread["message_retention_days"] == 90
    # Untouched preferences keep their defaults.
    assert reread["voice_replies"] is True


async def test_preferences_reject_an_invalid_theme(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_prefs3")

    response = await app_client.patch(
        PREFERENCES_URL, headers=bearer(token), json={"theme": "neon"}
    )
    assert response.status_code == 422


async def test_preferences_reject_an_unsupported_retention_window(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_prefs4")

    response = await app_client.patch(
        PREFERENCES_URL, headers=bearer(token), json={"message_retention_days": 7}
    )
    assert response.status_code == 422


async def test_preferences_are_per_user(app_client: AsyncClient, clean_db: None) -> None:
    alice_token = await token_for(app_client, "fb_pa", email="pa@example.test")
    bob_token = await token_for(app_client, "fb_pb", email="pb@example.test")

    await app_client.patch(PREFERENCES_URL, headers=bearer(alice_token), json={"theme": "dark"})

    bob = (await app_client.get(PREFERENCES_URL, headers=bearer(bob_token))).json()
    assert bob["theme"] == "system"


# ── relationships ─────────────────────────────────────────────────────────────
async def test_relationship_lifecycle(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_rel")

    created = await app_client.post(
        RELATIONSHIPS_URL,
        headers=bearer(token),
        json={"name": "Anita", "relation": "spouse", "notes": "anniversary in March"},
    )
    assert created.status_code == 201
    relationship_id = created.json()["id"]
    assert created.json()["ask_about"] is True
    assert created.json()["sensitivity"] == "normal"

    listed = (await app_client.get(RELATIONSHIPS_URL, headers=bearer(token))).json()
    assert len(listed["items"]) == 1

    patched = await app_client.patch(
        f"{RELATIONSHIPS_URL}/{relationship_id}",
        headers=bearer(token),
        json={"ask_about": False, "sensitivity": "high"},
    )
    assert patched.status_code == 200
    assert patched.json()["ask_about"] is False
    assert patched.json()["sensitivity"] == "high"
    assert patched.json()["name"] == "Anita"

    deleted = await app_client.delete(
        f"{RELATIONSHIPS_URL}/{relationship_id}", headers=bearer(token)
    )
    assert deleted.status_code == 204

    empty = (await app_client.get(RELATIONSHIPS_URL, headers=bearer(token))).json()
    assert empty["items"] == []


async def test_relationship_rejects_an_unknown_relation(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_rel2")

    response = await app_client.post(
        RELATIONSHIPS_URL, headers=bearer(token), json={"name": "X", "relation": "landlord"}
    )
    assert response.status_code == 422


async def test_one_user_cannot_see_anothers_relationships(
    app_client: AsyncClient, clean_db: None
) -> None:
    alice_token = await token_for(app_client, "fb_ra", email="ra@example.test")
    bob_token = await token_for(app_client, "fb_rb", email="rb@example.test")

    await app_client.post(
        RELATIONSHIPS_URL,
        headers=bearer(alice_token),
        json={"name": "Anita", "relation": "spouse"},
    )

    bob_list = (await app_client.get(RELATIONSHIPS_URL, headers=bearer(bob_token))).json()
    assert bob_list["items"] == []


async def test_one_user_cannot_read_anothers_relationship_by_id(
    app_client: AsyncClient, clean_db: None
) -> None:
    """404, not 403 — a 403 would confirm the id exists."""
    alice_token = await token_for(app_client, "fb_rc", email="rc@example.test")
    bob_token = await token_for(app_client, "fb_rd", email="rd@example.test")

    created = await app_client.post(
        RELATIONSHIPS_URL,
        headers=bearer(alice_token),
        json={"name": "Anita", "relation": "spouse", "notes": "private"},
    )
    relationship_id = created.json()["id"]

    for attempt in (
        app_client.patch(
            f"{RELATIONSHIPS_URL}/{relationship_id}",
            headers=bearer(bob_token),
            json={"name": "hijacked"},
        ),
        app_client.delete(f"{RELATIONSHIPS_URL}/{relationship_id}", headers=bearer(bob_token)),
    ):
        response = await attempt
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "relationship_not_found"

    # Alice's row is untouched.
    still_there = (await app_client.get(RELATIONSHIPS_URL, headers=bearer(alice_token))).json()
    assert still_there["items"][0]["name"] == "Anita"


async def test_unknown_relationship_id_is_not_found(
    app_client: AsyncClient, clean_db: None
) -> None:
    token = await token_for(app_client, "fb_rel3")

    response = await app_client.delete(f"{RELATIONSHIPS_URL}/{uuid.uuid4()}", headers=bearer(token))
    assert response.status_code == 404


# ── quotas ────────────────────────────────────────────────────────────────────
async def test_quotas_report_limits_and_zero_usage(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_quota")

    body = (await app_client.get(QUOTAS_URL, headers=bearer(token))).json()

    assert body["memories"] == {"used": 0, "limit": 1000}
    assert body["threads"] == {"used": 0, "limit": 500}
    # 2 GiB — the value that overflowed int4 before the column was widened.
    assert body["storage_bytes"]["limit"] == 2147483648


# ── consent ───────────────────────────────────────────────────────────────────
async def test_consent_is_an_append_only_ledger(app_client: AsyncClient, clean_db: None) -> None:
    """Withdrawal appends a row; the current state is the newest one."""
    token = await token_for(app_client, "fb_consent")

    granted = await app_client.post(
        CONSENTS_URL,
        headers=bearer(token),
        json={"kind": "memory_storage", "version": "2026-08-01", "granted": True},
    )
    assert granted.status_code == 201
    assert granted.json()["granted"] is True

    withdrawn = await app_client.post(
        CONSENTS_URL,
        headers=bearer(token),
        json={"kind": "memory_storage", "version": "2026-08-01", "granted": False},
    )
    assert withdrawn.status_code == 201

    current = (await app_client.get(CONSENTS_URL, headers=bearer(token))).json()["items"]
    assert len(current) == 1
    assert current[0]["kind"] == "memory_storage"
    assert current[0]["granted"] is False


async def test_consent_tracks_each_kind_separately(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_consent2")

    for kind in ("terms", "privacy", "voice_recording"):
        await app_client.post(
            CONSENTS_URL,
            headers=bearer(token),
            json={"kind": kind, "version": "1.0", "granted": True},
        )

    current = (await app_client.get(CONSENTS_URL, headers=bearer(token))).json()["items"]
    assert {row["kind"] for row in current} == {"terms", "privacy", "voice_recording"}


async def test_consent_rejects_an_unknown_kind(app_client: AsyncClient, clean_db: None) -> None:
    token = await token_for(app_client, "fb_consent3")

    response = await app_client.post(
        CONSENTS_URL,
        headers=bearer(token),
        json={"kind": "sell_my_data", "version": "1.0", "granted": True},
    )
    assert response.status_code == 422


async def test_one_user_cannot_see_anothers_consents(
    app_client: AsyncClient, clean_db: None
) -> None:
    alice_token = await token_for(app_client, "fb_ca", email="ca@example.test")
    bob_token = await token_for(app_client, "fb_cb", email="cb@example.test")

    await app_client.post(
        CONSENTS_URL,
        headers=bearer(alice_token),
        json={"kind": "location", "version": "1.0", "granted": True},
    )

    bob = (await app_client.get(CONSENTS_URL, headers=bearer(bob_token))).json()
    assert bob["items"] == []
