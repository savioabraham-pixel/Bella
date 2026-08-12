"""Reads and writes for the signed-in person.

Every function takes an already-scoped `AsyncSession` from `ScopedSession`, so RLS is
pinned to the caller before any statement runs. None of these queries needs an explicit
`WHERE user_id = ...` for safety — they carry one anyway, because a policy is a backstop
and not a substitute for saying what you mean.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.models.conversations import Thread
from app.db.models.identity import Profile, Relationship, User, UserPreferences, UserQuota
from app.db.models.memory import Memory
from app.db.models.system import Consent
from app.modules.me.schemas import (
    ConsentRecord,
    ConsentResponse,
    PreferencesResponse,
    PreferencesUpdate,
    ProfileResponse,
    ProfileUpdate,
    QuotasResponse,
    QuotaUsage,
    RelationshipCreate,
    RelationshipResponse,
    RelationshipUpdate,
)

logger = get_logger(__name__)

# Columns that live on `users` rather than `profiles`; a PATCH body mixes both freely and
# this is what decides where each one lands.
_USER_FIELDS = frozenset({"locale", "timezone"})


async def _load_profile(db: AsyncSession, user_id: uuid.UUID) -> tuple[User, Profile]:
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:  # pragma: no cover — the token proved this row exists moments ago
        raise NotFoundError("Account not found.", code="user_not_found")

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one_or_none()
    if profile is None:
        # Provisioning creates one; a missing row means a user predating that path.
        profile = Profile(user_id=user_id)
        db.add(profile)
        await db.flush()

    return user, profile


def _to_profile_response(user: User, profile: Profile) -> ProfileResponse:
    return ProfileResponse(
        id=user.id,
        display_id=user.display_id,
        email=user.email,
        email_verified=user.email_verified,
        phone_e164=user.phone_e164,
        phone_verified=user.phone_verified,
        role=user.role,
        status=user.status,
        locale=user.locale,
        timezone=user.timezone,
        is_minor=user.is_minor,
        created_at=user.created_at,
        last_seen_at=user.last_seen_at,
        full_name=profile.full_name,
        preferred_name=profile.preferred_name,
        pronunciation=profile.pronunciation,
        pronouns=profile.pronouns,
        gender=profile.gender,
        date_of_birth=profile.date_of_birth,
        bio=profile.bio,
        group_name=profile.group_name,
        onboarding_complete=bool(profile.preferred_name),
    )


async def get_profile(db: AsyncSession, user_id: uuid.UUID) -> ProfileResponse:
    user, profile = await _load_profile(db, user_id)
    return _to_profile_response(user, profile)


async def update_profile(
    db: AsyncSession, user_id: uuid.UUID, patch: ProfileUpdate
) -> ProfileResponse:
    """Apply only the keys the client actually sent.

    `exclude_unset` is what separates "clear this field" (key present, value null) from
    "leave it alone" (key absent). A client that PATCHes a whole rendered form would
    otherwise erase every field it does not know about.
    """
    user, profile = await _load_profile(db, user_id)

    changes = patch.model_dump(exclude_unset=True)
    for field, value in changes.items():
        target = user if field in _USER_FIELDS else profile
        setattr(target, field, value)

    await db.flush()
    logger.info("profile_updated", field_count=len(changes))
    return _to_profile_response(user, profile)


# ── preferences ───────────────────────────────────────────────────────────────
async def _load_preferences(db: AsyncSession, user_id: uuid.UUID) -> UserPreferences:
    preferences = (
        await db.execute(select(UserPreferences).where(UserPreferences.user_id == user_id))
    ).scalar_one_or_none()
    if preferences is None:
        preferences = UserPreferences(user_id=user_id)
        db.add(preferences)
        await db.flush()
    return preferences


async def get_preferences(db: AsyncSession, user_id: uuid.UUID) -> PreferencesResponse:
    return PreferencesResponse.model_validate(await _load_preferences(db, user_id))


async def update_preferences(
    db: AsyncSession, user_id: uuid.UUID, patch: PreferencesUpdate
) -> PreferencesResponse:
    preferences = await _load_preferences(db, user_id)

    changes = patch.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(preferences, field, value)

    await db.flush()
    logger.info("preferences_updated", field_count=len(changes))
    return PreferencesResponse.model_validate(preferences)


# ── relationships ─────────────────────────────────────────────────────────────
async def list_relationships(db: AsyncSession, user_id: uuid.UUID) -> list[RelationshipResponse]:
    rows = (
        (
            await db.execute(
                select(Relationship)
                .where(Relationship.user_id == user_id)
                .order_by(Relationship.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [RelationshipResponse.model_validate(row) for row in rows]


async def create_relationship(
    db: AsyncSession, user_id: uuid.UUID, body: RelationshipCreate
) -> RelationshipResponse:
    relationship = Relationship(user_id=user_id, **body.model_dump())
    db.add(relationship)
    await db.flush()
    logger.info("relationship_created", relation=body.relation, sensitivity=body.sensitivity)
    return RelationshipResponse.model_validate(relationship)


async def _get_relationship(
    db: AsyncSession, user_id: uuid.UUID, relationship_id: uuid.UUID
) -> Relationship:
    relationship = (
        await db.execute(
            select(Relationship).where(
                Relationship.id == relationship_id, Relationship.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if relationship is None:
        # 404 rather than 403 even when the row exists under another user: a 403 would
        # confirm the id is real.
        raise NotFoundError("No relationship with that id.", code="relationship_not_found")
    return relationship


async def update_relationship(
    db: AsyncSession,
    user_id: uuid.UUID,
    relationship_id: uuid.UUID,
    patch: RelationshipUpdate,
) -> RelationshipResponse:
    relationship = await _get_relationship(db, user_id, relationship_id)

    for field, value in patch.model_dump(exclude_unset=True).items():
        setattr(relationship, field, value)

    await db.flush()
    return RelationshipResponse.model_validate(relationship)


async def delete_relationship(
    db: AsyncSession, user_id: uuid.UUID, relationship_id: uuid.UUID
) -> None:
    relationship = await _get_relationship(db, user_id, relationship_id)
    await db.delete(relationship)
    await db.flush()


# ── quotas ────────────────────────────────────────────────────────────────────
async def get_quotas(db: AsyncSession, user_id: uuid.UUID) -> QuotasResponse:
    """Limits from `user_quotas`; usage counted live.

    Counting rather than trusting a stored counter is deliberate. The legacy client
    computed the meter in the browser and displayed the thread count under a label that
    said "memories"; a number that can drift from the rows it describes is worse than no
    number. The maintained counters become the source once Phase 4 keeps them honest.
    """
    quota = (
        await db.execute(select(UserQuota).where(UserQuota.user_id == user_id))
    ).scalar_one_or_none()
    if quota is None:
        quota = UserQuota(user_id=user_id)
        db.add(quota)
        await db.flush()

    memories_used = (
        await db.execute(select(func.count()).select_from(Memory).where(Memory.user_id == user_id))
    ).scalar_one()
    # Archived threads still occupy the quota — they are recoverable, so they still cost
    # storage. Only a real delete frees the slot.
    threads_used = (
        await db.execute(select(func.count()).select_from(Thread).where(Thread.user_id == user_id))
    ).scalar_one()

    return QuotasResponse(
        memories=QuotaUsage(used=memories_used, limit=quota.memories_limit),
        threads=QuotaUsage(used=threads_used, limit=quota.threads_limit),
        storage_bytes=QuotaUsage(used=quota.storage_bytes_used, limit=quota.storage_limit_bytes),
        tokens_today=QuotaUsage(used=quota.tokens_used_today, limit=quota.tokens_limit_daily),
    )


# ── consent ───────────────────────────────────────────────────────────────────
async def list_consents(db: AsyncSession, user_id: uuid.UUID) -> list[ConsentResponse]:
    """The current position on each kind — the newest row wins.

    The ledger keeps every grant and revocation; this collapses it to what is true now,
    which is what a settings screen needs to render.
    """
    newest = (
        select(
            Consent.kind,
            func.max(Consent.granted_at).label("granted_at"),
        )
        .where(Consent.user_id == user_id)
        .group_by(Consent.kind)
        .subquery()
    )
    rows = (
        (
            await db.execute(
                select(Consent)
                .join(
                    newest,
                    (Consent.kind == newest.c.kind) & (Consent.granted_at == newest.c.granted_at),
                )
                .where(Consent.user_id == user_id)
                .order_by(Consent.kind)
            )
        )
        .scalars()
        .all()
    )
    return [ConsentResponse.model_validate(row) for row in rows]


async def record_consent(
    db: AsyncSession,
    user_id: uuid.UUID,
    body: ConsentRecord,
    *,
    ip_digest: bytes | None,
    user_agent: str | None,
) -> ConsentResponse:
    """Append to the ledger. Nothing is ever updated in place.

    A consent record that can be rewritten is not evidence of anything, and under the DPDP
    Act the history of when a grant was given and withdrawn is the part that matters.
    """
    consent = Consent(
        user_id=user_id,
        kind=body.kind,
        version=body.version,
        granted=body.granted,
        granted_at=dt.datetime.now(tz=dt.UTC),
        ip_hash=ip_digest,
        user_agent=user_agent[:512] if user_agent else None,
    )
    db.add(consent)
    await db.flush()
    logger.info("consent_recorded", kind=body.kind, granted=body.granted, version=body.version)
    return ConsentResponse.model_validate(consent)
