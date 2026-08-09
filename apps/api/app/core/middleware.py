"""Middleware chain.

Order matters and is asserted in `main.py`: outermost is request context (so every log and
error carries an id), then security headers, then timing.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import Settings
from app.core.logging import get_logger, request_id_ctx, trace_id_ctx, user_id_ctx

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind it to the log context, and echo it to the client.

    The id is what a family member reads off an error screen when something breaks, so it
    must appear on the response even when the handler raised.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming if incoming and len(incoming) <= 64 else uuid.uuid4().hex
        trace_id = request.headers.get(TRACE_ID_HEADER) or request_id

        token_req = request_id_ctx.set(request_id)
        token_trace = trace_id_ctx.set(trace_id)
        token_user = user_id_ctx.set(None)
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token_req)
            trace_id_ctx.reset(token_trace)
            user_id_ctx.reset(token_user)

        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[TRACE_ID_HEADER] = trace_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """One structured line per request. Paths only — never query strings or bodies."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)

        route = request.scope.get("route")
        logger.info(
            "http_request",
            method=request.method,
            path=getattr(route, "path", request.url.path),
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers["Server-Timing"] = f"app;dur={duration_ms}"
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defence-in-depth headers on API responses.

    The API serves JSON, so the CSP here is deliberately absolute — it is the web app's
    own CSP (set in Next.js) that governs the rendered page.
    """

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        )
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault("Cache-Control", "no-store")
        if self._settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
            )
        return response
