"""Token primitives.

Two token types, deliberately different in kind:

**Access token** — a short-lived signed JWT the client sends on every request. Stateless by
design: verifying it costs no database round trip. The cost of statelessness is that a
revoked session stays valid until it expires, which is why revocation is mirrored into Redis
and checked in `app.core.deps` — a 15-minute window is acceptable for the convenience, a
30-day one would not be.

**Refresh token** — an opaque secret, never a JWT, because it is stored server-side and there
is nothing to gain from making its contents readable. Only a SHA-256 hash reaches the
database, so a dump of `sessions` cannot be replayed.

The refresh token carries its owner's id as a prefix:

    <user_id>.<secret>

That is a *routing hint*, not a claim. RLS needs `app.user_id` set before it will return the
session row, and the row cannot be found before the token is looked up — the prefix breaks
the cycle. It grants nothing: the lookup still matches on the hash of the whole string, and
scoping RLS to the claimed user means a forged prefix can only ever search that user's own
rows and find none.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Final

import jwt

from app.core.config import Settings
from app.core.errors import UnauthorizedError

# S105 flags these as hardcoded passwords on the name alone. They are a claim value and a
# delimiter; nothing secret is spelled out in this module.
ACCESS_TOKEN_TYPE: Final = "access"  # noqa: S105
_REFRESH_SECRET_BYTES: Final = 32
_TOKEN_SEPARATOR: Final = "."  # noqa: S105


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    """The verified contents of an access token."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    role: str
    expires_at: dt.datetime


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


# ── access tokens ─────────────────────────────────────────────────────────────
def mint_access_token(
    settings: Settings,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    role: str,
) -> tuple[str, int]:
    """Return `(token, expires_in_seconds)`."""
    issued_at = _now()
    expires_at = issued_at + dt.timedelta(seconds=settings.access_token_ttl_seconds)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "role": role,
        "typ": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": issued_at,
        "exp": expires_at,
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, settings.access_token_ttl_seconds


def verify_access_token(settings: Settings, token: str) -> AccessTokenClaims:
    """Verify signature, issuer, audience and expiry. Raises `UnauthorizedError`."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            # Pinning the algorithm is what stops a token signed with `alg: none`, or an
            # RS256 token verified with the public key as an HMAC secret, from being accepted.
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Your session has expired.", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("That token is not valid.", code="token_invalid") from exc

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        # A refresh token presented as a bearer credential, most likely.
        raise UnauthorizedError("Wrong token type.", code="token_invalid")

    try:
        user_id = uuid.UUID(payload["sub"])
        session_id = uuid.UUID(payload["sid"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("That token is not valid.", code="token_invalid") from exc

    return AccessTokenClaims(
        user_id=user_id,
        session_id=session_id,
        role=str(payload.get("role", "member")),
        expires_at=dt.datetime.fromtimestamp(payload["exp"], tz=dt.UTC),
    )


# ── refresh tokens ────────────────────────────────────────────────────────────
def generate_refresh_token(user_id: uuid.UUID) -> tuple[str, bytes]:
    """Return `(token, sha256_digest)`. Only the digest is ever persisted."""
    token = f"{user_id}{_TOKEN_SEPARATOR}{secrets.token_urlsafe(_REFRESH_SECRET_BYTES)}"
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def parse_refresh_token_owner(token: str) -> uuid.UUID | None:
    """Read the routing prefix. Returns None for anything malformed."""
    owner, separator, _ = token.partition(_TOKEN_SEPARATOR)
    if not separator:
        return None
    try:
        return uuid.UUID(owner)
    except ValueError:
        return None


def refresh_tokens_match(presented: str, stored_digest: bytes) -> bool:
    """Constant-time comparison, so a timing signal cannot reveal a stored digest."""
    return hmac.compare_digest(hash_refresh_token(presented), stored_digest)


# ── request metadata ──────────────────────────────────────────────────────────
def hash_ip(settings: Settings, ip: str | None) -> bytes | None:
    """Keyed hash of a client IP.

    Sessions need "was this the same device?" without holding an address that is itself
    personal data. Keying with the JWT secret means a database dump cannot be reversed by
    hashing the ~4 billion IPv4 addresses.
    """
    if not ip:
        return None
    return hmac.new(
        settings.jwt_secret.get_secret_value().encode("utf-8"),
        ip.encode("utf-8"),
        hashlib.sha256,
    ).digest()
