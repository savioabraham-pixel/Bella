"""Profile, preferences, relationships, quotas and consent.

Every route here is authenticated and scoped by `ScopedSession`, so there is no path on
which one person's id can be supplied to read another's row — the id comes from the token
and nowhere else.

`POST /me/avatar` is absent until Phase 5: it needs presigned object storage, and an upload
endpoint that streams through the API is the thing that design deliberately avoids.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status

from app.core.deps import ClientIp, CurrentUser, ScopedSession, SettingsDep
from app.core.security import hash_ip
from app.modules.me import service
from app.modules.me.schemas import (
    ConsentListResponse,
    ConsentRecord,
    ConsentResponse,
    PreferencesResponse,
    PreferencesUpdate,
    ProfileResponse,
    ProfileUpdate,
    QuotasResponse,
    RelationshipCreate,
    RelationshipListResponse,
    RelationshipResponse,
    RelationshipUpdate,
)

router = APIRouter()

_MAX_USER_AGENT = 512


@router.get("", response_model=ProfileResponse, summary="Read the signed-in profile")
async def read_profile(db: ScopedSession, principal: CurrentUser) -> ProfileResponse:
    return await service.get_profile(db, principal.user_id)


@router.patch("", response_model=ProfileResponse, summary="Update the signed-in profile")
async def update_profile(
    patch: ProfileUpdate, db: ScopedSession, principal: CurrentUser
) -> ProfileResponse:
    return await service.update_profile(db, principal.user_id, patch)


# ── preferences ───────────────────────────────────────────────────────────────
@router.get("/preferences", response_model=PreferencesResponse, summary="Read preferences")
async def read_preferences(db: ScopedSession, principal: CurrentUser) -> PreferencesResponse:
    return await service.get_preferences(db, principal.user_id)


@router.patch("/preferences", response_model=PreferencesResponse, summary="Update preferences")
async def update_preferences(
    patch: PreferencesUpdate, db: ScopedSession, principal: CurrentUser
) -> PreferencesResponse:
    return await service.update_preferences(db, principal.user_id, patch)


# ── relationships ─────────────────────────────────────────────────────────────
@router.get(
    "/relationships",
    response_model=RelationshipListResponse,
    summary="People Bella may ask about",
)
async def list_relationships(db: ScopedSession, principal: CurrentUser) -> RelationshipListResponse:
    return RelationshipListResponse(items=await service.list_relationships(db, principal.user_id))


@router.post(
    "/relationships",
    response_model=RelationshipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add someone Bella may ask about",
)
async def create_relationship(
    body: RelationshipCreate, db: ScopedSession, principal: CurrentUser
) -> RelationshipResponse:
    return await service.create_relationship(db, principal.user_id, body)


@router.patch(
    "/relationships/{relationship_id}",
    response_model=RelationshipResponse,
    summary="Update a relationship",
)
async def update_relationship(
    relationship_id: uuid.UUID,
    patch: RelationshipUpdate,
    db: ScopedSession,
    principal: CurrentUser,
) -> RelationshipResponse:
    return await service.update_relationship(db, principal.user_id, relationship_id, patch)


@router.delete(
    "/relationships/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a relationship",
)
async def delete_relationship(
    relationship_id: uuid.UUID, db: ScopedSession, principal: CurrentUser
) -> Response:
    await service.delete_relationship(db, principal.user_id, relationship_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── quotas ────────────────────────────────────────────────────────────────────
@router.get("/quotas", response_model=QuotasResponse, summary="Usage against limits")
async def read_quotas(db: ScopedSession, principal: CurrentUser) -> QuotasResponse:
    return await service.get_quotas(db, principal.user_id)


# ── consent ───────────────────────────────────────────────────────────────────
@router.get("/consents", response_model=ConsentListResponse, summary="Current consent state")
async def list_consents(db: ScopedSession, principal: CurrentUser) -> ConsentListResponse:
    return ConsentListResponse(items=await service.list_consents(db, principal.user_id))


@router.post(
    "/consents",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant or withdraw a consent",
)
async def record_consent(
    body: ConsentRecord,
    request: Request,
    db: ScopedSession,
    principal: CurrentUser,
    settings: SettingsDep,
    ip: ClientIp,
) -> ConsentResponse:
    user_agent = request.headers.get("user-agent")
    return await service.record_consent(
        db,
        principal.user_id,
        body,
        ip_digest=hash_ip(settings, ip),
        user_agent=user_agent[:_MAX_USER_AGENT] if user_agent else None,
    )
