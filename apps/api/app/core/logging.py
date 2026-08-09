"""Structured logging.

Two rules this module enforces so individual call sites cannot get them wrong:

1. Every log line carries the request context (request_id, trace_id, user_id) via
   contextvars — no threading it through call signatures.
2. Sensitive values are redacted by a processor in the pipeline, not at the call site.
   Message content, memory content, document text, tokens, phone numbers and emails
   must never reach a log sink.
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from app.core.config import Settings

# ── request-scoped context ────────────────────────────────────────────────────
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_ctx: ContextVar[str | None] = ContextVar("trace_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)

# ── redaction ─────────────────────────────────────────────────────────────────
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "api_key",
        "secret",
        "jwt_secret",
        "cookie",
        "set-cookie",
        "content",
        "message",
        "text",
        "prompt",
        "memory",
        "memories",
        "transcript",
        "email",
        "phone",
        "phone_e164",
        "full_name",
        "preferred_name",
        "date_of_birth",
        "dob",
        "lat",
        "lon",
        "latitude",
        "longitude",
    }
)

REDACTED = "[redacted]"

# Structural fields are never free text, so they are exempt from pattern scrubbing.
# Without this, an ISO timestamp ("2026-08-09T06:58:16Z") trips the phone matcher and
# every log line loses its timestamp.
STRUCTURAL_KEYS = frozenset(
    {
        "timestamp",
        "level",
        "event",
        "logger",
        "request_id",
        "trace_id",
        "user_id",
        "method",
        "path",
        "route",
        "status_code",
        "duration_ms",
        "error_type",
        "dependency",
    }
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# Bounded by non-word characters so it cannot start mid-token, and validated by digit
# count below — a date has eight digits, an E.164 number has nine to fifteen.
_PHONE_RE = re.compile(r"(?<![\w.-])\+?\d[\d\s()-]{7,}\d(?![\w.-])")
_BEARER_RE = re.compile(r"(?i)bearer\s+[\w\-.]+")
_NON_DIGITS = re.compile(r"\D")

_PHONE_MIN_DIGITS = 9
_PHONE_MAX_DIGITS = 15
# An undecorated digit run this long is a phone number rather than an id or a byte count.
_PHONE_MIN_BARE_DIGITS = 10


def _replace_if_phone(match: re.Match[str]) -> str:
    """Only redact when the shape is plausible for a real number.

    Guards against collateral damage to dates, durations, byte counts and ids, which the
    surrounding pattern would otherwise match. A `+` prefix or internal separators are
    strong signals; an undecorated run of digits has to be longer before it is treated
    as a number rather than an identifier.

    This is defence in depth, not the primary control — `phone` and `phone_e164` are
    dropped by key before any text scrubbing happens.
    """
    candidate = match.group()
    digits = _NON_DIGITS.sub("", candidate)
    if not _PHONE_MIN_DIGITS <= len(digits) <= _PHONE_MAX_DIGITS:
        return candidate

    formatted = candidate.startswith("+") or len(digits) != len(candidate)
    if formatted or len(digits) >= _PHONE_MIN_BARE_DIGITS:
        return "[phone]"
    return candidate


def _scrub_text(value: str) -> str:
    value = _BEARER_RE.sub("Bearer [redacted]", value)
    value = _EMAIL_RE.sub("[email]", value)
    return _PHONE_RE.sub(_replace_if_phone, value)


def redact_processor(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """Drop sensitive keys and scrub obvious PII patterns from free text."""
    for key in list(event_dict.keys()):
        lowered = key.lower()
        if lowered in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
        elif lowered in STRUCTURAL_KEYS:
            continue
        elif isinstance(event_dict[key], str):
            event_dict[key] = _scrub_text(event_dict[key])
    return event_dict


def context_processor(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """Attach request-scoped identifiers to every line."""
    if (request_id := request_id_ctx.get()) is not None:
        event_dict["request_id"] = request_id
    if (trace_id := trace_id_ctx.get()) is not None:
        event_dict["trace_id"] = trace_id
    if (user_id := user_id_ctx.get()) is not None:
        event_dict["user_id"] = user_id
    return event_dict


def configure_logging(settings: Settings) -> None:
    """Install the structlog pipeline and route stdlib logging through it."""
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        context_processor,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        redact_processor,
    ]

    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            *shared,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Route stdlib loggers (uvicorn, sqlalchemy, alembic) through the same pipeline.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    for noisy in ("uvicorn.access", "uvicorn.error", "sqlalchemy.engine", "asyncio"):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
