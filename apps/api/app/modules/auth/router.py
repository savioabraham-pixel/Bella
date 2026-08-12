"""Auth routes.

The refresh token travels as an HttpOnly cookie rather than in the response body, so a
successful XSS cannot read it — the reason the access token is short-lived and the refresh
token is never exposed to JavaScript. `/session` and `/refresh` are the only unauthenticated
endpoints here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status

from app.core.config import Settings
from app.core.deps import ClientIp, CurrentUser, SettingsDep
from app.core.errors import UnauthorizedError
from app.core.logging import get_logger
from app.modules.auth import service
from app.modules.auth.firebase import get_verifier
from app.modules.auth.schemas import (
    RevocationResponse,
    SessionListResponse,
    SessionRequest,
    SessionResponse,
)

logger = get_logger(__name__)
router = APIRouter()

_MAX_USER_AGENT = 512


def _set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_token_ttl_seconds,
        path=settings.refresh_cookie_path,
        domain=settings.refresh_cookie_domain,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        # Lax rather than Strict: the OAuth redirect back from Google is a cross-site
        # top-level navigation, and Strict would drop the cookie on exactly that hop.
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        domain=settings.refresh_cookie_domain,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _user_agent(request: Request) -> str | None:
    agent = request.headers.get("user-agent")
    return agent[:_MAX_USER_AGENT] if agent else None


@router.post(
    "/session",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange a Firebase ID token for a Bella session",
)
async def create_session(
    body: SessionRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    ip: ClientIp,
) -> SessionResponse:
    claims = await get_verifier(settings).verify(body.id_token)
    result, refresh_token = await service.establish_session(
        settings,
        claims,
        device_name=body.device_name,
        user_agent=_user_agent(request),
        ip=ip,
    )
    _set_refresh_cookie(response, settings, refresh_token)
    return result


@router.post(
    "/refresh",
    response_model=SessionResponse,
    summary="Rotate the refresh cookie and return a new access token",
)
async def refresh_session(
    request: Request,
    response: Response,
    settings: SettingsDep,
    ip: ClientIp,
) -> SessionResponse:
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise UnauthorizedError("Please sign in again.", code="refresh_missing")

    try:
        result, new_token = await service.rotate_session(
            settings, token, user_agent=_user_agent(request), ip=ip
        )
    except UnauthorizedError:
        # The cookie is spent either way — a stale one only produces confusing retries.
        _clear_refresh_cookie(response, settings)
        raise

    _set_refresh_cookie(response, settings, new_token)
    return result


@router.post("/logout", response_model=RevocationResponse, summary="Revoke this session")
async def logout(
    response: Response,
    settings: SettingsDep,
    principal: CurrentUser,
    ip: ClientIp,
) -> RevocationResponse:
    revoked = await service.revoke_session(
        settings, user_id=principal.user_id, session_id=principal.session_id, ip=ip
    )
    _clear_refresh_cookie(response, settings)
    return RevocationResponse(revoked=revoked)


@router.post(
    "/logout-all",
    response_model=RevocationResponse,
    summary="Revoke every session for this user",
)
async def logout_all(
    response: Response,
    settings: SettingsDep,
    principal: CurrentUser,
    ip: ClientIp,
) -> RevocationResponse:
    revoked = await service.revoke_all_sessions(settings, user_id=principal.user_id, ip=ip)
    _clear_refresh_cookie(response, settings)
    return RevocationResponse(revoked=revoked)


@router.get("/sessions", response_model=SessionListResponse, summary="List active sessions")
async def list_sessions(principal: CurrentUser) -> SessionListResponse:
    items = await service.list_sessions(
        user_id=principal.user_id, current_session_id=principal.session_id
    )
    return SessionListResponse(items=items)


@router.delete(
    "/sessions/{session_id}",
    response_model=RevocationResponse,
    summary="Revoke one session by id",
)
async def revoke_session(
    session_id: uuid.UUID,
    settings: SettingsDep,
    principal: CurrentUser,
    ip: ClientIp,
) -> RevocationResponse:
    revoked = await service.revoke_session(
        settings, user_id=principal.user_id, session_id=session_id, ip=ip
    )
    return RevocationResponse(revoked=revoked)
