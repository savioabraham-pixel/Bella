"""Opaque keyset cursors.

Offset pagination is wrong for a feed that changes underneath the reader: inserting a row
shifts every later page, so the client sees duplicates or holes. Keyset pagination asks for
"the rows after this exact position", which stays correct however much the table moves.

The cursor is base64url-encoded JSON so it is URL-safe and obviously not meant to be
constructed by hand. It is *not* signed — it encodes only a sort position the caller could
have observed anyway, and every query it feeds is RLS-scoped, so a forged cursor can at
worst page through the caller's own rows in a strange order.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import json
from typing import Any

from app.core.errors import BadRequestError

MAX_LIMIT = 100
DEFAULT_LIMIT = 50


def encode_cursor(values: list[Any]) -> str:
    payload = json.dumps(values, separators=(",", ":"), default=_serialise)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> list[Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        values = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BadRequestError("That cursor is not valid.", code="invalid_cursor") from exc

    if not isinstance(values, list):
        raise BadRequestError("That cursor is not valid.", code="invalid_cursor")
    return values


def _serialise(value: object) -> str:
    if isinstance(value, dt.datetime):
        return value.isoformat()
    return str(value)


def parse_datetime(value: object) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise BadRequestError("That cursor is not valid.", code="invalid_cursor") from exc
    # A naive value would compare against a timestamptz column as UTC anyway; being
    # explicit avoids depending on that.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
