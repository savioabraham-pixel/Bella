"""Request and response bodies for `/me`.

Every PATCH body distinguishes "not supplied" from "explicitly cleared", which a plain
`str | None = None` cannot: `model_fields_set` carries that distinction, so sending
`{"pronunciation": null}` erases the value while omitting the key leaves it alone. Without
that, a client that renders a form and PATCHes the whole thing would silently wipe every
field it does not know about — which is how the legacy app lost preferences on every
release.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models.identity import RELATION_KINDS
from app.db.models.system import CONSENT_KINDS

Theme = Literal["light", "dark", "system"]
RetentionDays = Literal[30, 90, 365]
Relation = Literal["spouse", "child", "parent", "sibling", "friend", "colleague", "other"]
Sensitivity = Literal["normal", "high"]
ConsentKind = Literal[
    "terms", "privacy", "memory_storage", "voice_recording", "location", "analytics", "marketing"
]


def _assert_matches_database(name: str, literal: object, allowed: tuple[str, ...]) -> None:
    """Fail at import if a Literal drifts from its CHECK constraint.

    Otherwise the drift surfaces as a 500 from Postgres at write time, on whichever value
    someone forgot to add in the other place.
    """
    if set(get_args(literal)) != set(allowed):
        raise RuntimeError(f"{name} does not match the database constraint: {allowed}")


_assert_matches_database("Relation", Relation, RELATION_KINDS)
_assert_matches_database("ConsentKind", ConsentKind, CONSENT_KINDS)

Name = Annotated[str, Field(min_length=1, max_length=200)]


def _validate_timezone(value: str | None) -> str | None:
    """IANA zones only.

    The legacy client computed offsets by hand and got DST wrong twice a year; storing a
    zone name rather than an offset is what fixes that, and it is only worth anything if
    the name actually resolves.
    """
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"{value!r} is not a known IANA time zone") from exc
    return value


# ── profile ───────────────────────────────────────────────────────────────────
class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_id: str
    email: str | None
    email_verified: bool
    phone_e164: str | None
    phone_verified: bool
    role: str
    status: str
    locale: str
    timezone: str
    is_minor: bool
    created_at: dt.datetime
    last_seen_at: dt.datetime | None

    full_name: str | None
    preferred_name: str | None
    pronunciation: str | None
    pronouns: str | None
    gender: str | None
    date_of_birth: dt.date | None
    bio: str | None
    group_name: str | None
    onboarding_complete: bool


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: Name | None = None
    preferred_name: Name | None = None
    pronunciation: Annotated[str, Field(max_length=200)] | None = None
    pronouns: Annotated[str, Field(max_length=50)] | None = None
    gender: Annotated[str, Field(max_length=50)] | None = None
    date_of_birth: dt.date | None = None
    bio: Annotated[str, Field(max_length=10_000)] | None = None
    locale: Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")] | None = None
    timezone: Annotated[str, Field(max_length=64)] | None = None

    _check_timezone = field_validator("timezone")(_validate_timezone)

    @field_validator("date_of_birth")
    @classmethod
    def _not_in_the_future(cls, value: dt.date | None) -> dt.date | None:
        if value is not None and value > dt.datetime.now(tz=dt.UTC).date():
            raise ValueError("date of birth cannot be in the future")
        return value


# ── preferences ───────────────────────────────────────────────────────────────
class PreferencesResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    theme: Theme
    clock_24h: bool
    voice_replies: bool
    voice_autoplay: bool
    hands_free: bool
    notifications_enabled: bool
    location_enabled: bool
    message_retention_days: RetentionDays | None


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: Theme | None = None
    clock_24h: bool | None = None
    voice_replies: bool | None = None
    voice_autoplay: bool | None = None
    hands_free: bool | None = None
    notifications_enabled: bool | None = None
    location_enabled: bool | None = None
    # null is meaningful here: it means "keep messages forever".
    message_retention_days: RetentionDays | None = None


# ── relationships ─────────────────────────────────────────────────────────────
class RelationshipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    relation: str
    notes: str | None
    ask_about: bool
    sensitivity: str
    created_at: dt.datetime


class RelationshipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    relation: Relation
    notes: Annotated[str, Field(max_length=2_000)] | None = None
    ask_about: bool = True
    # Bereavements and medical detail were hardcoded into the legacy bundle with no marking
    # at all. Flagging them is what lets the prompt builder handle them carefully.
    sensitivity: Sensitivity = "normal"


class RelationshipUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    relation: Relation | None = None
    notes: Annotated[str, Field(max_length=2_000)] | None = None
    ask_about: bool | None = None
    sensitivity: Sensitivity | None = None


class RelationshipListResponse(BaseModel):
    items: list[RelationshipResponse]


# ── quotas ────────────────────────────────────────────────────────────────────
class QuotaUsage(BaseModel):
    used: int
    limit: int


class QuotasResponse(BaseModel):
    memories: QuotaUsage
    threads: QuotaUsage
    storage_bytes: QuotaUsage
    tokens_today: QuotaUsage


# ── consent ───────────────────────────────────────────────────────────────────
class ConsentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str
    version: str
    granted: bool
    granted_at: dt.datetime


class ConsentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ConsentKind
    version: Annotated[str, Field(min_length=1, max_length=20)]
    granted: bool


class ConsentListResponse(BaseModel):
    items: list[ConsentResponse]
