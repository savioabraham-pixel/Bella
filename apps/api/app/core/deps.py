"""Request-scoped dependencies.

`CurrentUser` is the only way a route learns who is calling. There is no code path that
reads an identity from a request body, a query parameter or a header other than the verified
`Authorization` bearer token — which is what makes finding C2 structurally impossible to
reintroduce rather than merely fixed once.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.logging import user_id_ctx
from app.core.security import verify_access_token
from app.db.session import session_for_user

# auto_error=False so a missing header produces our error envelope rather than FastAPI's
# bare `{"detail": ...}`.
_bearer = HTTPBearer(auto_error=False, scheme_name="BellaAccessToken")

ROLE_ORDER: dict[str, int] = {"member": 0, "admin": 1, "owner": 2}


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller. Everything here came out of a verified signature."""

    user_id: uuid.UUID
    session_id: uuid.UUID
    role: str

    def has_role(self, minimum: str) -> bool:
        return ROLE_ORDER.get(self.role, -1) >= ROLE_ORDER[minimum]


SettingsDep = Annotated[Settings, Depends(get_settings)]


def client_ip(request: Request) -> str | None:
    """Left-most X-Forwarded-For entry, else the socket address.

    Trustworthy only because Cloud Run terminates the connection and rewrites the header;
    the value is hashed before storage in any case, so a spoofed one costs nothing.
    """
    if forwarded := request.headers.get("x-forwarded-for"):
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def get_principal(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Sign in to continue.", code="missing_token")

    claims = verify_access_token(settings, credentials.credentials)

    # Imported here rather than at module scope: the auth module imports `deps` for its own
    # routes, and a top-level import would close the cycle.
    from app.modules.auth.service import is_session_revoked

    if await is_session_revoked(claims.session_id):
        raise UnauthorizedError("Your session has ended.", code="session_revoked")

    # Every log line for the rest of this request now carries the user id.
    user_id_ctx.set(str(claims.user_id))

    return Principal(
        user_id=claims.user_id,
        session_id=claims.session_id,
        role=claims.role,
    )


CurrentUser = Annotated[Principal, Depends(get_principal)]


async def get_scoped_session(principal: CurrentUser) -> AsyncGenerator[AsyncSession, None]:
    """A database session with RLS pinned to the caller for the whole transaction."""
    async with session_for_user(principal.user_id) as session:
        yield session


ScopedSession = Annotated[AsyncSession, Depends(get_scoped_session)]


def require_role(minimum: str) -> object:
    """Route guard for the admin surface. Every use is audited at the call site."""

    async def _guard(principal: CurrentUser) -> Principal:
        if not principal.has_role(minimum):
            raise ForbiddenError("You do not have permission to do that.")
        return principal

    return Depends(_guard)


ClientIp = Annotated[str | None, Depends(client_ip)]
