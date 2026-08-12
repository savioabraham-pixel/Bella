"""Request and response bodies for the auth routes.

Note what is absent: no endpoint accepts a user id, an email or a `profileKey`. The subject
is always derived from a verified token. That is the structural fix for finding C2 — it is
not possible to ask for someone else's session because there is nowhere to name them.
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class SessionRequest(BaseModel):
    id_token: str = Field(min_length=1, max_length=4096)
    device_name: str | None = Field(default=None, max_length=200)


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_id: str
    preferred_name: str | None
    role: str
    locale: str
    timezone: str
    onboarding_complete: bool


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 — the scheme name, not a credential
    expires_in: int
    user: AuthenticatedUser
    flags: dict[str, bool] = Field(default_factory=dict)


class SessionSummary(BaseModel):
    """One active session, for the "where am I signed in?" screen."""

    id: uuid.UUID
    device_name: str | None
    created_at: dt.datetime
    last_used_at: dt.datetime | None
    expires_at: dt.datetime
    current: bool


class SessionListResponse(BaseModel):
    items: list[SessionSummary]


class RevocationResponse(BaseModel):
    revoked: int
