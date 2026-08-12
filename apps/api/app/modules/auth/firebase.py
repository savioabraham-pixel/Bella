"""Firebase ID token verification.

Firebase ID tokens are ordinary RS256 JWTs signed by Google, so they are verified here
directly against Google's published x509 certificates rather than through `firebase-admin`.
Two reasons: the admin SDK's verification path is synchronous and would have to be pushed
into a thread pool on every request, and it pulls a large dependency tree for one function.
What it does that this does not is check whether the account was disabled or the token
revoked since issue — a Firebase-side round trip we do not want on the hot path anyway.
Our own `users.status` is the authority on whether an account may sign in.

The checks below are the ones Firebase documents as required. Skipping any of them —
particularly the audience and issuer — would accept a token minted for a different project.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Final, Protocol

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
from cryptography.x509 import load_pem_x509_certificate
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.errors import ServiceUnavailableError, UnauthorizedError
from app.core.logging import get_logger

logger = get_logger(__name__)

_ALGORITHM: Final = "RS256"
_MAX_UID_LENGTH: Final = 128
_CACHE_CONTROL_MAX_AGE = re.compile(r"max-age=(\d+)")
_CERT_FETCH_TIMEOUT_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class IdentityClaims:
    """What the identity provider asserts. Nothing here is trusted as authorisation."""

    uid: str
    email: str | None = None
    email_verified: bool = False
    phone_number: str | None = None
    display_name: str | None = None
    sign_in_provider: str | None = None


class IdentityVerifier(Protocol):
    async def verify(self, id_token: str) -> IdentityClaims: ...


def _claims_from_payload(payload: dict[str, Any]) -> IdentityClaims:
    uid = payload.get("sub") or ""
    if not uid or len(uid) > _MAX_UID_LENGTH:
        raise UnauthorizedError("That sign-in token is not valid.", code="id_token_invalid")

    firebase = payload.get("firebase") or {}
    return IdentityClaims(
        uid=uid,
        email=payload.get("email"),
        email_verified=bool(payload.get("email_verified", False)),
        phone_number=payload.get("phone_number"),
        display_name=payload.get("name"),
        sign_in_provider=firebase.get("sign_in_provider") if isinstance(firebase, dict) else None,
    )


class FirebaseVerifier:
    """Verifies against Google's signing certificates, cached in-process.

    The cache is per-worker rather than in Redis on purpose: the certificates are public,
    a few kilobytes, and rotate daily, so a per-process copy costs nothing and removes
    Redis from the authentication path entirely.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.firebase_project_id:
            raise RuntimeError("FIREBASE_PROJECT_ID is required when AUTH_PROVIDER=firebase.")
        self._settings = settings
        self._project_id = settings.firebase_project_id
        self._issuer = f"https://securetoken.google.com/{settings.firebase_project_id}"
        self._keys: dict[str, PublicKeyTypes] = {}
        self._expires_at: dt.datetime = dt.datetime.min.replace(tzinfo=dt.UTC)
        self._lock = asyncio.Lock()

    async def verify(self, id_token: str) -> IdentityClaims:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError(
                "That sign-in token is not valid.", code="id_token_invalid"
            ) from exc

        if header.get("alg") != _ALGORITHM:
            raise UnauthorizedError("That sign-in token is not valid.", code="id_token_invalid")

        kid = header.get("kid")
        if not kid:
            raise UnauthorizedError("That sign-in token is not valid.", code="id_token_invalid")

        key = await self._public_key(kid)
        if key is None:
            # Unknown kid after a forced refresh: either a rotation we have not seen or a
            # forgery. Both are 401 — we cannot tell them apart, and retrying is the
            # client's job.
            raise UnauthorizedError("That sign-in token is not valid.", code="id_token_invalid")

        try:
            payload = jwt.decode(
                id_token,
                key,  # type: ignore[arg-type]  # PyJWT accepts a cryptography public key
                algorithms=[_ALGORITHM],
                audience=self._project_id,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError(
                "That sign-in token has expired. Please sign in again.",
                code="id_token_expired",
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError(
                "That sign-in token is not valid.", code="id_token_invalid"
            ) from exc

        return _claims_from_payload(payload)

    async def _public_key(self, kid: str) -> PublicKeyTypes | None:
        now = dt.datetime.now(tz=dt.UTC)
        if kid in self._keys and now < self._expires_at:
            return self._keys[kid]

        async with self._lock:
            # Another coroutine may have refreshed while this one waited for the lock.
            if kid in self._keys and dt.datetime.now(tz=dt.UTC) < self._expires_at:
                return self._keys[kid]
            await self._refresh()

        return self._keys.get(kid)

    async def _refresh(self) -> None:
        try:
            certificates, max_age = await self._fetch_certificates()
        except httpx.HTTPError as exc:
            logger.error("firebase_certs_fetch_failed", error_type=type(exc).__name__)
            if self._keys:
                # Serve stale rather than lock everyone out of a working application
                # because Google was briefly unreachable.
                logger.warning("firebase_certs_serving_stale")
                return
            raise ServiceUnavailableError(
                "Sign-in is temporarily unavailable.", code="identity_provider_unavailable"
            ) from exc

        keys: dict[str, PublicKeyTypes] = {}
        for kid, pem in certificates.items():
            try:
                keys[kid] = load_pem_x509_certificate(pem.encode("utf-8")).public_key()
            except ValueError:
                logger.warning("firebase_cert_unparseable", kid=kid)

        if keys:
            self._keys = keys
            ttl = min(max_age, self._settings.firebase_certs_max_age_seconds)
            self._expires_at = dt.datetime.now(tz=dt.UTC) + dt.timedelta(seconds=ttl)
            logger.info("firebase_certs_refreshed", key_count=len(keys), ttl_seconds=ttl)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.2, max=2), reraise=True)
    async def _fetch_certificates(self) -> tuple[dict[str, str], int]:
        async with httpx.AsyncClient(timeout=_CERT_FETCH_TIMEOUT_SECONDS) as client:
            response = await client.get(self._settings.firebase_certs_url)
            response.raise_for_status()
            certificates: dict[str, str] = response.json()

        max_age = self._settings.firebase_certs_max_age_seconds
        if match := _CACHE_CONTROL_MAX_AGE.search(response.headers.get("cache-control", "")):
            max_age = int(match.group(1))
        return certificates, max_age


class MockVerifier:
    """Local and test only. Reads an unsigned token's claims and believes them.

    Deliberately shaped like a real ID token so that every layer above it — the router,
    the service, the client — takes the identical code path in development and production.
    `Settings` refuses to boot a deployed environment with this selected.
    """

    async def verify(self, id_token: str) -> IdentityClaims:
        try:
            payload = jwt.decode(
                id_token,
                options={"verify_signature": False, "verify_exp": True, "require": ["sub"]},
                algorithms=[_ALGORITHM, "HS256"],
            )
        except jwt.ExpiredSignatureError as exc:
            raise UnauthorizedError(
                "That sign-in token has expired.", code="id_token_expired"
            ) from exc
        except jwt.InvalidTokenError as exc:
            raise UnauthorizedError(
                "That sign-in token is not valid.", code="id_token_invalid"
            ) from exc
        return _claims_from_payload(payload)


_verifier: IdentityVerifier | None = None


def get_verifier(settings: Settings) -> IdentityVerifier:
    """One verifier per process, so the certificate cache is shared across requests."""
    global _verifier
    if _verifier is None:
        _verifier = (
            FirebaseVerifier(settings) if settings.auth_provider == "firebase" else MockVerifier()
        )
        logger.info("identity_verifier_ready", provider=settings.auth_provider)
    return _verifier


def reset_verifier() -> None:
    """Test hook — drops the cached verifier so settings can change between cases."""
    global _verifier
    _verifier = None
