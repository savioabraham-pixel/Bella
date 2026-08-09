"""One error envelope for the entire API.

    {"error": {"code": "thread_not_found",
               "message": "No thread with that id.",
               "request_id": "01J8X...",
               "details": {}}}

`code` is a stable machine-readable string; clients switch on it. `message` is safe to show
a user. Internal failures never leak a stack trace or a database message to the client — they
are logged with the request_id, which the UI surfaces so a report is traceable.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_ctx

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for every deliberate failure in the application."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        super().__init__(self.message)


# ── 4xx ───────────────────────────────────────────────────────────────────────
class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"
    message = "The request was malformed."


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Authentication is required."


class ForbiddenError(AppError):
    """Only for actions the caller may not perform on a resource they can see.

    Never use this to signal that another user's resource exists — that is a
    NotFoundError, so the response cannot be used to enumerate other people's data.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to do that."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "That conflicts with the current state."


class QuotaExceededError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "quota_exceeded"
    message = "You have reached your limit."


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Please slow down."


# ── 5xx ───────────────────────────────────────────────────────────────────────
class ProviderError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"
    message = "An upstream service failed."


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "The service is temporarily unavailable."


def _envelope(
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_ctx.get(),
        }
    }
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        log = logger.warning if exc.status_code < 500 else logger.error
        log("app_error", code=exc.code, status_code=exc.status_code, details=exc.details)
        return _envelope(exc.code, exc.message, exc.status_code, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in err["loc"][1:]) or "body",
                "reason": err["msg"],
            }
            for err in exc.errors()
        ]
        logger.warning("validation_error", field_count=len(fields))
        return _envelope(
            "validation_error",
            "Some fields were invalid.",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"fields": fields},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {
            400: "bad_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            429: "rate_limited",
        }
        code = codes.get(exc.status_code, "http_error")
        return _envelope(code, str(exc.detail), exc.status_code)

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_request: Request, exc: IntegrityError) -> JSONResponse:
        # The database rejected the write. Log the cause; tell the client nothing about it.
        logger.warning("integrity_error", constraint=getattr(exc.orig, "constraint_name", None))
        return _envelope(
            "conflict",
            "That conflicts with existing data.",
            status.HTTP_409_CONFLICT,
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database_error", error_type=type(exc).__name__)
        return _envelope(
            "database_error",
            "A database error occurred.",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", error_type=type(exc).__name__)
        return _envelope(
            "internal_error",
            "Something went wrong on our side.",
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
