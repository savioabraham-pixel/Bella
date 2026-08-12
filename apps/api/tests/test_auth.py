"""Session issue, rotation, revocation and isolation.

These run against the real database through the ASGI app, because the properties worth
asserting — that RLS scopes a revocation, that a rotated refresh token kills its family —
only exist when Postgres is in the loop. Mocking the session store would test the mock.

Refresh cookies are passed as an explicit `Cookie` header rather than through httpx's jar:
the cookie is `Secure`, the test transport is `http://test`, and a cookie jar will
correctly refuse to send it. Setting the header directly keeps the test about rotation
rather than about transport.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import jwt
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.config import Settings

pytestmark = [pytest.mark.integration, pytest.mark.security]

SESSION_URL = "/v1/auth/session"
REFRESH_URL = "/v1/auth/refresh"
SESSIONS_URL = "/v1/auth/sessions"
LOGOUT_URL = "/v1/auth/logout"
LOGOUT_ALL_URL = "/v1/auth/logout-all"


def make_id_token(
    uid: str,
    *,
    email: str | None = None,
    name: str | None = None,
    expires_in: int = 3600,
) -> str:
    """An unsigned-in-practice ID token, shaped like Firebase's.

    The mock verifier reads the claims without checking the signature, so the key is
    arbitrary — what matters is that the payload has the same shape the real provider
    emits, so the layers above take an identical path.
    """
    now = dt.datetime.now(tz=dt.UTC)
    payload: dict[str, Any] = {
        "sub": uid,
        "iat": now,
        "exp": now + dt.timedelta(seconds=expires_in),
        "firebase": {"sign_in_provider": "google.com"},
    }
    if email:
        payload["email"] = email
        payload["email_verified"] = True
    if name:
        payload["name"] = name
    return jwt.encode(payload, "not-the-signing-key", algorithm="HS256")


def refresh_cookie(response: Response, settings: Settings) -> str:
    token = response.cookies.get(settings.refresh_cookie_name)
    assert token, "no refresh cookie was set"
    return token


def cookie_header(settings: Settings, token: str) -> dict[str, str]:
    return {"Cookie": f"{settings.refresh_cookie_name}={token}"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def sign_in(
    client: AsyncClient,
    uid: str,
    *,
    email: str | None = None,
    name: str | None = None,
) -> Response:
    response = await client.post(
        SESSION_URL,
        json={"id_token": make_id_token(uid, email=email, name=name), "device_name": "pytest"},
    )
    assert response.status_code == 200, response.text
    return response


# ── provisioning ──────────────────────────────────────────────────────────────
async def test_first_sign_in_provisions_a_user(
    app_client: AsyncClient, clean_db: None, admin_conn: AsyncConnection
) -> None:
    response = await sign_in(app_client, "fb_new", email="new@example.test", name="Savio")
    body = response.json()

    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert body["user"]["display_id"].startswith("BE-")
    assert body["user"]["preferred_name"] == "Savio"
    assert body["user"]["role"] == "member"
    assert body["user"]["onboarding_complete"] is True

    # The profile and quota rows are created in the same transaction, not lazily later.
    user_id = uuid.UUID(body["user"]["id"])
    counts = await admin_conn.execute(
        text("""
            SELECT (SELECT count(*) FROM profiles WHERE user_id = :uid) AS profiles,
                   (SELECT count(*) FROM user_quotas WHERE user_id = :uid) AS quotas
        """),
        {"uid": user_id},
    )
    row = counts.mappings().one()
    assert row["profiles"] == 1
    assert row["quotas"] == 1


async def test_sign_in_without_a_name_leaves_onboarding_incomplete(
    app_client: AsyncClient, clean_db: None
) -> None:
    body = (await sign_in(app_client, "fb_nameless")).json()
    assert body["user"]["preferred_name"] is None
    assert body["user"]["onboarding_complete"] is False


async def test_second_sign_in_reuses_the_same_user(app_client: AsyncClient, clean_db: None) -> None:
    first = (await sign_in(app_client, "fb_repeat", email="repeat@example.test")).json()
    second = (await sign_in(app_client, "fb_repeat", email="repeat@example.test")).json()

    assert first["user"]["id"] == second["user"]["id"]
    assert first["user"]["display_id"] == second["user"]["display_id"]
    assert first["access_token"] != second["access_token"]


async def test_suspended_account_cannot_sign_in(
    app_client: AsyncClient, clean_db: None, admin_conn: AsyncConnection
) -> None:
    body = (await sign_in(app_client, "fb_suspended")).json()
    await admin_conn.execute(
        text("UPDATE users SET status = 'suspended' WHERE id = :uid"),
        {"uid": uuid.UUID(body["user"]["id"])},
    )
    await admin_conn.commit()

    response = await app_client.post(SESSION_URL, json={"id_token": make_id_token("fb_suspended")})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_inactive"


async def test_malformed_id_token_is_rejected(app_client: AsyncClient, clean_db: None) -> None:
    response = await app_client.post(SESSION_URL, json={"id_token": "not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "id_token_invalid"


async def test_expired_id_token_is_rejected(app_client: AsyncClient, clean_db: None) -> None:
    response = await app_client.post(
        SESSION_URL, json={"id_token": make_id_token("fb_stale", expires_in=-60)}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "id_token_expired"


# ── access tokens ─────────────────────────────────────────────────────────────
async def test_protected_route_requires_a_token(app_client: AsyncClient) -> None:
    response = await app_client.get(SESSIONS_URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_token"


async def test_protected_route_rejects_a_forged_token(app_client: AsyncClient) -> None:
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "sid": str(uuid.uuid4()),
            "typ": "access",
            "iss": "bella-api",
            "aud": "bella-app",
            "iat": dt.datetime.now(tz=dt.UTC),
            "exp": dt.datetime.now(tz=dt.UTC) + dt.timedelta(hours=1),
        },
        "the-wrong-signing-key",
        algorithm="HS256",
    )
    response = await app_client.get(SESSIONS_URL, headers=bearer(forged))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_invalid"


async def test_access_token_lists_only_its_own_sessions(
    app_client: AsyncClient, clean_db: None
) -> None:
    alice = (await sign_in(app_client, "fb_alice", email="alice@example.test")).json()
    await sign_in(app_client, "fb_bob", email="bob@example.test")

    response = await app_client.get(SESSIONS_URL, headers=bearer(alice["access_token"]))
    assert response.status_code == 200

    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["current"] is True
    assert items[0]["device_name"] == "pytest"


# ── rotation ──────────────────────────────────────────────────────────────────
async def test_refresh_rotates_the_cookie(
    app_client: AsyncClient, clean_db: None, settings: Settings
) -> None:
    first = await sign_in(app_client, "fb_rotate")
    original = refresh_cookie(first, settings)

    second = await app_client.post(REFRESH_URL, headers=cookie_header(settings, original))
    assert second.status_code == 200

    rotated = refresh_cookie(second, settings)
    assert rotated != original
    assert second.json()["user"]["id"] == first.json()["user"]["id"]


async def test_refresh_without_a_cookie_is_rejected(app_client: AsyncClient) -> None:
    response = await app_client.post(REFRESH_URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "refresh_missing"


async def test_reusing_a_rotated_token_revokes_the_family(
    app_client: AsyncClient, clean_db: None, settings: Settings
) -> None:
    """The stolen-token case.

    A thief and the honest holder both end up signed out, which is the correct trade —
    the alternative leaves an attacker with a valid lineage indefinitely.
    """
    first = await sign_in(app_client, "fb_thief")
    original = refresh_cookie(first, settings)

    second = await app_client.post(REFRESH_URL, headers=cookie_header(settings, original))
    rotated = refresh_cookie(second, settings)

    replayed = await app_client.post(REFRESH_URL, headers=cookie_header(settings, original))
    assert replayed.status_code == 401
    assert replayed.json()["error"]["code"] == "refresh_reused"

    # The successor is dead too, not just the replayed token.
    followup = await app_client.post(REFRESH_URL, headers=cookie_header(settings, rotated))
    assert followup.status_code == 401

    # And the access token minted from that family no longer opens anything.
    denied = await app_client.get(SESSIONS_URL, headers=bearer(second.json()["access_token"]))
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "session_revoked"


async def test_refresh_token_for_a_nonexistent_user_is_rejected(
    app_client: AsyncClient, clean_db: None, settings: Settings
) -> None:
    """A well-formed token naming someone else's id must not resolve to a session."""
    forged = f"{uuid.uuid4()}.{'x' * 43}"
    response = await app_client.post(REFRESH_URL, headers=cookie_header(settings, forged))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "refresh_invalid"


# ── revocation ────────────────────────────────────────────────────────────────
async def test_logout_revokes_the_current_access_token(
    app_client: AsyncClient, clean_db: None
) -> None:
    body = (await sign_in(app_client, "fb_logout")).json()
    token = body["access_token"]

    assert (await app_client.get(SESSIONS_URL, headers=bearer(token))).status_code == 200

    logout = await app_client.post(LOGOUT_URL, headers=bearer(token))
    assert logout.status_code == 200
    assert logout.json()["revoked"] == 1

    after = await app_client.get(SESSIONS_URL, headers=bearer(token))
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "session_revoked"


async def test_logout_all_revokes_every_session(app_client: AsyncClient, clean_db: None) -> None:
    first = (await sign_in(app_client, "fb_many")).json()
    second = (await sign_in(app_client, "fb_many")).json()

    response = await app_client.post(LOGOUT_ALL_URL, headers=bearer(second["access_token"]))
    assert response.status_code == 200
    assert response.json()["revoked"] == 2

    for token in (first["access_token"], second["access_token"]):
        assert (await app_client.get(SESSIONS_URL, headers=bearer(token))).status_code == 401


async def test_one_user_cannot_revoke_another_users_session(
    app_client: AsyncClient, clean_db: None
) -> None:
    """RLS scopes the UPDATE, so Bob's session id matches nothing under Alice's context.

    This is the assertion that would have caught the legacy `profileKey` defect: naming
    someone else's resource id is not enough, and it does not even leak that it exists.
    """
    alice = (await sign_in(app_client, "fb_alice2", email="alice2@example.test")).json()
    bob = (await sign_in(app_client, "fb_bob2", email="bob2@example.test")).json()

    bob_sessions = (await app_client.get(SESSIONS_URL, headers=bearer(bob["access_token"]))).json()[
        "items"
    ]
    bob_session_id = bob_sessions[0]["id"]

    attack = await app_client.delete(
        f"{SESSIONS_URL}/{bob_session_id}", headers=bearer(alice["access_token"])
    )
    assert attack.status_code == 200
    assert attack.json()["revoked"] == 0

    # Bob is still signed in.
    assert (
        await app_client.get(SESSIONS_URL, headers=bearer(bob["access_token"]))
    ).status_code == 200


async def test_session_creation_is_audited(
    app_client: AsyncClient, clean_db: None, admin_conn: AsyncConnection
) -> None:
    body = (await sign_in(app_client, "fb_audited")).json()

    actions = (
        (
            await admin_conn.execute(
                text("SELECT action FROM audit_log WHERE target_user_id = :uid"),
                {"uid": uuid.UUID(body["user"]["id"])},
            )
        )
        .scalars()
        .all()
    )
    assert "auth.user.provisioned" in actions
