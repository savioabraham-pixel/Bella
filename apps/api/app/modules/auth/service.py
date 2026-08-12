"""Session lifecycle: provision, issue, rotate, revoke.

Every database statement here runs inside `session_for_user`, so Row-Level Security is
active even on the path that creates the user. Provisioning works because the new id is
generated in Python and set as `app.user_id` *before* the insert — the policy's WITH CHECK
then passes for exactly the row being written and nothing else.

The one lookup that cannot be scoped, resolving a Firebase UID to a user id before any user
id is known, goes through the `auth_lookup_user` SECURITY DEFINER function rather than
through a hole in the policy.

Two ordering rules this module has to respect, both of which are easy to get wrong:

* `app.user_id` is set with `set_config(..., true)`, which is **transaction-local**. Rolling
  back inside a scoped session discards it, and every subsequent statement in that block
  then fails its RLS check. Recoverable failures use SAVEPOINTs, never `rollback()`.
* Raising out of `session_for_user` rolls the transaction back. Anything that must persist
  *and* end in an error — reuse detection revoking a token family is the case here — has to
  leave the block cleanly and raise afterwards.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.logging import get_logger, request_id_ctx
from app.core.redis import get_redis
from app.core.security import (
    generate_refresh_token,
    hash_ip,
    hash_refresh_token,
    mint_access_token,
    parse_refresh_token_owner,
)
from app.db.models.identity import Profile, Session, User, UserPreferences, UserQuota
from app.db.models.system import AuditLog, FeatureFlag
from app.db.session import get_session_factory, session_for_user
from app.modules.auth.firebase import IdentityClaims
from app.modules.auth.schemas import (
    AuthenticatedUser,
    SessionResponse,
    SessionSummary,
)

logger = get_logger(__name__)

_DISPLAY_ID_ATTEMPTS = 5
_SIGNED_OUT_STATUSES = frozenset({"suspended", "deleted"})


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _revocation_key(session_id: uuid.UUID) -> str:
    return f"revoked_session:{session_id}"


async def _deny_access_tokens(settings: Settings, session_ids: list[uuid.UUID]) -> None:
    """Mirror revocation into Redis.

    Access tokens are stateless, so a revoked session would otherwise stay usable until its
    token expires. The denylist entry only has to outlive the longest token that could
    still be in flight, hence the access TTL rather than the refresh TTL.
    """
    if not session_ids:
        return
    redis = get_redis()
    async with redis.pipeline(transaction=False) as pipe:
        for session_id in session_ids:
            pipe.setex(_revocation_key(session_id), settings.access_token_ttl_seconds, "1")
        await pipe.execute()


async def is_session_revoked(session_id: uuid.UUID) -> bool:
    return bool(await get_redis().exists(_revocation_key(session_id)))


def _generate_display_id() -> str:
    """Human-quotable, non-sequential. Not a secret, and never used for authorisation."""
    return f"BE-{secrets.token_hex(4).upper()}"


def _audit(
    db: AsyncSession,
    *,
    action: str,
    user_id: uuid.UUID,
    role: str | None = None,
    resource_id: uuid.UUID | None = None,
    ip_digest: bytes | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_user_id=user_id,
            actor_role=role,
            action=action,
            resource_type="session",
            resource_id=resource_id,
            target_user_id=user_id,
            audit_metadata=metadata or {},
            ip_hash=ip_digest,
            request_id=request_id_ctx.get(),
        )
    )


async def _load_flags(db: AsyncSession, user_id: uuid.UUID) -> dict[str, bool]:
    """Global flags plus per-user allowlist. Percentage rollouts land with the admin API."""
    rows = (await db.execute(select(FeatureFlag))).scalars().all()
    return {flag.key: bool(flag.enabled or user_id in (flag.user_allowlist or [])) for flag in rows}


async def _resolve_existing_user(firebase_uid: str) -> tuple[uuid.UUID, str] | None:
    """UID → (user_id, status). Runs unscoped through the SECURITY DEFINER function.

    This is the only statement in the application that legitimately runs without
    `app.user_id`, which is why it is a dedicated function with a fixed search_path rather
    than a relaxed policy.
    """
    async with get_session_factory()() as db:
        result = await db.execute(
            text("SELECT id, status, role FROM auth_lookup_user(:uid)"),
            {"uid": firebase_uid},
        )
        row = result.mappings().first()

    if row is None:
        return None
    return uuid.UUID(str(row["id"])), str(row["status"])


async def _provision_user(db: AsyncSession, user_id: uuid.UUID, claims: IdentityClaims) -> User:
    """Create the identity, profile and quota rows for a first-time sign-in.

    Accounts are never linked by email address. An unverified email from one provider
    matching a verified one from another is precisely how account takeover works, and the
    19 existing family members are migrated by UID rather than discovered here.

    The display id is random, so a collision is possible if unlikely. Each attempt runs in
    a SAVEPOINT: a unique-violation rolls back only the failed INSERT, leaving the enclosing
    transaction — and the `app.user_id` setting that RLS depends on — intact.
    """
    user: User | None = None

    for attempt in range(_DISPLAY_ID_ATTEMPTS):
        candidate = User(
            id=user_id,
            firebase_uid=claims.uid,
            email=claims.email,
            email_verified=claims.email_verified,
            phone_e164=claims.phone_number,
            phone_verified=bool(claims.phone_number),
            display_id=_generate_display_id(),
        )
        try:
            async with db.begin_nested():
                db.add(candidate)
                await db.flush()
        except IntegrityError as exc:
            constraint = getattr(exc.orig, "constraint_name", "") or ""
            is_display_id_clash = "display_id" in constraint
            if not is_display_id_clash or attempt == _DISPLAY_ID_ATTEMPTS - 1:
                logger.warning("user_provisioning_conflict", constraint=constraint)
                raise ConflictError(
                    "That account could not be created.", code="account_conflict"
                ) from exc
            continue
        user = candidate
        break

    if user is None:  # pragma: no cover — the loop either breaks or raises
        raise ConflictError("That account could not be created.", code="account_conflict")

    db.add(Profile(user_id=user_id, preferred_name=claims.display_name))
    db.add(UserQuota(user_id=user_id))
    db.add(UserPreferences(user_id=user_id))
    await db.flush()

    logger.info("user_provisioned", provider=claims.sign_in_provider)
    return user


async def _create_session(
    db: AsyncSession,
    settings: Settings,
    *,
    user_id: uuid.UUID,
    family_id: uuid.UUID,
    device_name: str | None,
    user_agent: str | None,
    ip_digest: bytes | None,
) -> tuple[Session, str]:
    refresh_token, digest = generate_refresh_token(user_id)
    session = Session(
        user_id=user_id,
        refresh_token_hash=digest,
        family_id=family_id,
        device_name=device_name,
        user_agent=user_agent[:512] if user_agent else None,
        ip_hash=ip_digest,
        expires_at=_now() + dt.timedelta(seconds=settings.refresh_token_ttl_seconds),
        last_used_at=_now(),
    )
    db.add(session)
    await db.flush()
    return session, refresh_token


async def _prune_sessions(
    db: AsyncSession, settings: Settings, user_id: uuid.UUID, keep: uuid.UUID
) -> None:
    """Hold the active session count at the configured ceiling, oldest revoked first."""
    active = (
        (
            await db.execute(
                select(Session.id)
                .where(
                    Session.user_id == user_id,
                    Session.revoked_at.is_(None),
                    Session.expires_at > _now(),
                    Session.id != keep,
                )
                .order_by(Session.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    excess = list(active[settings.max_sessions_per_user - 1 :])
    if not excess:
        return

    await db.execute(update(Session).where(Session.id.in_(excess)).values(revoked_at=_now()))
    await _deny_access_tokens(settings, excess)
    logger.info("sessions_pruned", count=len(excess))


async def _build_response(
    db: AsyncSession,
    settings: Settings,
    *,
    user: User,
    session_id: uuid.UUID,
) -> SessionResponse:
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user.id))
    ).scalar_one_or_none()

    access_token, expires_in = mint_access_token(
        settings, user_id=user.id, session_id=session_id, role=user.role
    )
    return SessionResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=AuthenticatedUser(
            id=user.id,
            display_id=user.display_id,
            preferred_name=profile.preferred_name if profile else None,
            role=user.role,
            locale=user.locale,
            timezone=user.timezone,
            # Onboarding is complete once Bella knows what to call you — the one thing the
            # legacy registration flow actually collected before it could greet anyone.
            onboarding_complete=bool(profile and profile.preferred_name),
        ),
        flags=await _load_flags(db, user.id),
    )


# ── public operations ─────────────────────────────────────────────────────────
async def establish_session(
    settings: Settings,
    claims: IdentityClaims,
    *,
    device_name: str | None,
    user_agent: str | None,
    ip: str | None,
) -> tuple[SessionResponse, str]:
    """Exchange a verified identity for a Bella session. Returns `(body, refresh_token)`."""
    ip_digest = hash_ip(settings, ip)
    existing = await _resolve_existing_user(claims.uid)

    if existing is not None and existing[1] in _SIGNED_OUT_STATUSES:
        # Deliberately specific: a suspended person needs to know why they cannot get in,
        # and the account provably exists to them already.
        raise ForbiddenError("This account is not currently active.", code="account_inactive")

    user_id = existing[0] if existing else uuid.uuid4()
    is_new = existing is None

    async with session_for_user(user_id) as db:
        if is_new:
            user = await _provision_user(db, user_id, claims)
        else:
            user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            # Keep the contact details in step with the provider, which owns them.
            user.email = claims.email or user.email
            user.email_verified = claims.email_verified or user.email_verified
            user.phone_e164 = claims.phone_number or user.phone_e164

        user.last_seen_at = _now()

        session, refresh_token = await _create_session(
            db,
            settings,
            user_id=user_id,
            family_id=uuid.uuid4(),
            device_name=device_name,
            user_agent=user_agent,
            ip_digest=ip_digest,
        )
        await _prune_sessions(db, settings, user_id, keep=session.id)
        _audit(
            db,
            action="auth.user.provisioned" if is_new else "auth.session.created",
            user_id=user_id,
            role=user.role,
            resource_id=session.id,
            ip_digest=ip_digest,
            metadata={"provider": claims.sign_in_provider, "new_user": is_new},
        )
        body = await _build_response(db, settings, user=user, session_id=session.id)

    return body, refresh_token


async def rotate_session(
    settings: Settings,
    refresh_token: str,
    *,
    user_agent: str | None,
    ip: str | None,
) -> tuple[SessionResponse, str]:
    """Rotate a refresh token. Presenting an already-rotated one kills the whole family."""
    owner_id = parse_refresh_token_owner(refresh_token)
    if owner_id is None:
        raise UnauthorizedError("Please sign in again.", code="refresh_invalid")

    digest = hash_refresh_token(refresh_token)
    ip_digest = hash_ip(settings, ip)

    # Set inside the transaction, raised after it commits — see the module docstring.
    failure: tuple[str, str] | None = None
    result: tuple[SessionResponse, str] | None = None

    async with session_for_user(owner_id) as db:
        session = (
            await db.execute(select(Session).where(Session.refresh_token_hash == digest))
        ).scalar_one_or_none()

        if session is None:
            failure = ("Please sign in again.", "refresh_invalid")
        elif session.revoked_at is not None:
            # This token was already exchanged. Either it leaked or it is a replay; the
            # honest holder is signed out too, which is the correct trade against a thief
            # holding a valid lineage.
            revoked = await _revoke_family(db, settings, session.family_id)
            _audit(
                db,
                action="auth.refresh.reuse_detected",
                user_id=owner_id,
                resource_id=session.id,
                ip_digest=ip_digest,
                metadata={"family_id": str(session.family_id), "revoked": revoked},
            )
            logger.warning("refresh_token_reuse", family_id=str(session.family_id))
            failure = ("Please sign in again.", "refresh_reused")
        elif session.expires_at <= _now():
            failure = ("Please sign in again.", "refresh_expired")
        else:
            user = (await db.execute(select(User).where(User.id == owner_id))).scalar_one_or_none()
            if user is None or user.status in _SIGNED_OUT_STATUSES:
                failure = ("Please sign in again.", "refresh_invalid")
            else:
                session.revoked_at = _now()
                session.last_used_at = _now()
                await _deny_access_tokens(settings, [session.id])

                successor, new_refresh_token = await _create_session(
                    db,
                    settings,
                    user_id=owner_id,
                    family_id=session.family_id,
                    device_name=session.device_name,
                    user_agent=user_agent,
                    ip_digest=ip_digest,
                )
                user.last_seen_at = _now()
                body = await _build_response(db, settings, user=user, session_id=successor.id)
                result = (body, new_refresh_token)

    if failure is not None:
        raise UnauthorizedError(failure[0], code=failure[1])
    if result is None:  # pragma: no cover — one of the two branches is always taken
        raise UnauthorizedError("Please sign in again.", code="refresh_invalid")
    return result


async def _revoke_family(db: AsyncSession, settings: Settings, family_id: uuid.UUID) -> int:
    ids = (
        (
            await db.execute(
                select(Session.id).where(
                    Session.family_id == family_id, Session.revoked_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    if ids:
        await db.execute(update(Session).where(Session.id.in_(ids)).values(revoked_at=_now()))
        await _deny_access_tokens(settings, list(ids))
    return len(ids)


async def revoke_session(
    settings: Settings, *, user_id: uuid.UUID, session_id: uuid.UUID, ip: str | None
) -> int:
    """Sign out one session. RLS scopes the statement, so another user's id matches nothing."""
    async with session_for_user(user_id) as db:
        session = (
            await db.execute(select(Session).where(Session.id == session_id))
        ).scalar_one_or_none()
        if session is None or session.revoked_at is not None:
            return 0

        session.revoked_at = _now()
        await _deny_access_tokens(settings, [session.id])
        _audit(
            db,
            action="auth.session.revoked",
            user_id=user_id,
            resource_id=session_id,
            ip_digest=hash_ip(settings, ip),
        )
        return 1


async def revoke_all_sessions(settings: Settings, *, user_id: uuid.UUID, ip: str | None) -> int:
    async with session_for_user(user_id) as db:
        ids = (
            (
                await db.execute(
                    select(Session.id).where(
                        Session.user_id == user_id, Session.revoked_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        if ids:
            await db.execute(update(Session).where(Session.id.in_(ids)).values(revoked_at=_now()))
            await _deny_access_tokens(settings, list(ids))
        _audit(
            db,
            action="auth.session.revoked_all",
            user_id=user_id,
            ip_digest=hash_ip(settings, ip),
            metadata={"count": len(ids)},
        )
        return len(ids)


async def list_sessions(
    *, user_id: uuid.UUID, current_session_id: uuid.UUID
) -> list[SessionSummary]:
    async with session_for_user(user_id) as db:
        rows = (
            (
                await db.execute(
                    select(Session)
                    .where(
                        Session.user_id == user_id,
                        Session.revoked_at.is_(None),
                        Session.expires_at > func.now(),
                    )
                    .order_by(Session.last_used_at.desc().nulls_last())
                )
            )
            .scalars()
            .all()
        )
        return [
            SessionSummary(
                id=row.id,
                device_name=row.device_name,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
                current=row.id == current_session_id,
            )
            for row in rows
        ]
