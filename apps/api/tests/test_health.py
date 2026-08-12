"""Health and error-envelope contract."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_liveness_returns_ok(app_client: AsyncClient) -> None:
    response = await app_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "bella-api"
    assert body["uptime_seconds"] >= 0


async def test_liveness_does_not_touch_dependencies(app_client: AsyncClient) -> None:
    """Liveness must not report on the database or Redis.

    If it did, a slow dependency would cause the orchestrator to kill containers that
    are perfectly capable of serving.
    """
    body = (await app_client.get("/health")).json()

    assert "dependencies" not in body


async def test_readiness_reports_each_dependency(app_client: AsyncClient) -> None:
    response = await app_client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert set(body["dependencies"]) == {"database", "redis"}
    assert all(dep["healthy"] for dep in body["dependencies"].values())


async def test_request_id_is_echoed(app_client: AsyncClient) -> None:
    """The id on the response is what a user reads off an error screen."""
    response = await app_client.get("/health", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"


async def test_request_id_is_generated_when_absent(app_client: AsyncClient) -> None:
    response = await app_client.get("/health")

    assert len(response.headers["X-Request-ID"]) == 32


async def test_security_headers_present(app_client: AsyncClient) -> None:
    headers = (await app_client.get("/health")).headers

    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


async def test_unknown_route_uses_the_error_envelope(app_client: AsyncClient) -> None:
    response = await app_client.get("/v1/does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"]


@pytest.mark.integration
async def test_api_responses_forbid_loading_anything(app_client: AsyncClient) -> None:
    """The API serves JSON. Nothing it returns should be able to fetch a resource."""
    response = await app_client.get("/health")
    assert response.headers["content-security-policy"] == (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )


@pytest.mark.integration
async def test_the_docs_page_may_load_its_own_bundle(app_client: AsyncClient) -> None:
    """Swagger UI loads its bundle from a CDN.

    Under the API's blanket `default-src 'none'` the browser blocks the script and the
    stylesheet, and /docs renders as a blank page — which is exactly what it did until
    this exemption existed.
    """
    response = await app_client.get("/docs")
    policy = response.headers["content-security-policy"]

    assert "https://cdn.jsdelivr.net" in policy
    assert "script-src 'self' https://cdn.jsdelivr.net" in policy
    # Widened for the bundle, not thrown open.
    assert "default-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
