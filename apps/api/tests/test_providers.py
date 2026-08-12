"""The provider gateway: SSE parsing, error classification, and failover.

These are unit tests against a mock transport rather than integration tests. The Gemini
adapter's real logic is parsing a stream and deciding which failures are worth retrying
elsewhere — both verifiable without spending money, and neither verifiable at all if the
only way to run them is to call a paid API.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import ProviderError
from app.providers.base import GenerationRequest, Turn, collect
from app.providers.gemini import GeminiProvider
from app.providers.mock import MockProvider
from app.providers.registry import generate, reset_providers

pytestmark = [pytest.mark.unit]

BASE_ENV = {
    "DATABASE_URL": "postgresql://u:p@db:5432/bella",
    "REDIS_URL": "redis://cache:6379/0",
}


def make_settings(**overrides: str) -> Settings:
    return Settings(**{**BASE_ENV, **overrides})  # type: ignore[arg-type]


def make_request(prompt: str = "hello") -> GenerationRequest:
    return GenerationRequest(
        system="You are Bella.",
        turns=[Turn(role="user", content=prompt)],
        model="gemini-2.5-flash",
        max_output_tokens=256,
        temperature=0.7,
    )


def sse(*chunks: dict[str, Any]) -> bytes:
    return "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks).encode()


def text_chunk(text: str) -> dict[str, Any]:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


@pytest.fixture(autouse=True)
def _clear_provider_cache() -> None:
    reset_providers()


def gemini_with(handler: Any, **overrides: str) -> GeminiProvider:
    settings = make_settings(LLM_API_KEY="test-key", **overrides)
    return GeminiProvider(settings, transport=httpx.MockTransport(handler))


# ── mock provider ─────────────────────────────────────────────────────────────
async def test_mock_provider_streams_and_reports_usage() -> None:
    result = await collect(MockProvider(), make_request("what is the plan"))

    assert "what is the plan" in result.text
    assert result.finish_reason == "stop"
    assert result.input_tokens and result.input_tokens > 0
    assert result.output_tokens and result.output_tokens > 0
    # Streaming timing is only meaningful if more than one delta arrived.
    assert result.first_token_ms is not None


async def test_mock_provider_can_be_asked_to_fail() -> None:
    with pytest.raises(ProviderError):
        await collect(MockProvider(), make_request("__provider_error__"))


# ── gemini: parsing ───────────────────────────────────────────────────────────
async def test_gemini_assembles_a_reply_from_its_deltas() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=sse(
                text_chunk("Good "),
                text_chunk("morning, "),
                {
                    "candidates": [
                        {"content": {"parts": [{"text": "Savio."}]}, "finishReason": "STOP"}
                    ],
                    "usageMetadata": {"promptTokenCount": 42, "candidatesTokenCount": 7},
                    "modelVersion": "gemini-2.5-flash-001",
                },
            ),
        )

    result = await collect(gemini_with(handler), make_request())

    assert result.text == "Good morning, Savio."
    assert result.finish_reason == "stop"
    assert result.input_tokens == 42
    assert result.output_tokens == 7
    assert result.model == "gemini-2.5-flash-001"


async def test_gemini_sends_the_system_prompt_as_a_system_instruction() -> None:
    """Not as a fake first turn, which is what the legacy client did on every request."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=sse(text_chunk("ok")))

    await collect(gemini_with(handler), make_request())

    assert captured["system_instruction"]["parts"][0]["text"] == "You are Bella."
    assert [turn["role"] for turn in captured["contents"]] == ["user"]


async def test_gemini_maps_assistant_turns_to_the_model_role() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=sse(text_chunk("ok")))

    request = GenerationRequest(
        system="s",
        turns=[
            Turn(role="user", content="one"),
            Turn(role="assistant", content="two"),
            Turn(role="user", content="three"),
        ],
        model="gemini-2.5-flash",
        max_output_tokens=64,
        temperature=0.5,
    )
    await collect(gemini_with(handler), request)

    assert [turn["role"] for turn in captured["contents"]] == ["user", "model", "user"]


async def test_gemini_sends_the_api_key_as_a_header() -> None:
    """It never reaches a browser — the whole reason the gateway is server-side."""
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200, content=sse(text_chunk("ok")))

    await collect(gemini_with(handler), make_request())

    assert captured["x-goog-api-key"] == "test-key"
    assert "key=" not in str(captured.get("host", ""))


async def test_gemini_ignores_unparseable_chunks() -> None:
    """A malformed frame must not lose the whole reply."""

    def handler(_request: httpx.Request) -> httpx.Response:
        body = b"data: {not json}\n\n" + sse(text_chunk("still here"))
        return httpx.Response(200, content=body)

    result = await collect(gemini_with(handler), make_request())
    assert result.text == "still here"


# ── gemini: failure classification ────────────────────────────────────────────
@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (429, "provider_unavailable"),
        (503, "provider_unavailable"),
        (500, "provider_unavailable"),
        (400, "provider_rejected"),
        (403, "provider_rejected"),
    ],
)
async def test_gemini_classifies_http_failures(status_code: int, expected_code: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "nope"}})

    with pytest.raises(ProviderError) as raised:
        await collect(gemini_with(handler), make_request())

    assert raised.value.code == expected_code


async def test_gemini_reports_a_timeout_as_such() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow")

    with pytest.raises(ProviderError) as raised:
        await collect(gemini_with(handler), make_request())

    assert raised.value.code == "provider_timeout"


async def test_gemini_reports_an_unreachable_host_as_such() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderError) as raised:
        await collect(gemini_with(handler), make_request())

    assert raised.value.code == "provider_unreachable"


async def test_gemini_does_not_log_the_vendors_error_body() -> None:
    """The body can quote the prompt back, which would put message content in a log."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "blocked: the biopsy result"}})

    with pytest.raises(ProviderError) as raised:
        await collect(gemini_with(handler), make_request())

    assert "biopsy" not in raised.value.message
    assert "biopsy" not in json.dumps(raised.value.details)


# ── failover ──────────────────────────────────────────────────────────────────
async def test_a_transient_failure_falls_back_to_the_second_vendor() -> None:
    settings = make_settings(
        LLM_PROVIDER="gemini",
        LLM_API_KEY="test-key",
        LLM_FALLBACK_PROVIDER="mock",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    # Seed the cache with a transport-injected primary so no real request is attempted.
    from app.providers import registry

    registry._providers[f"gemini:{settings.llm_model}"] = GeminiProvider(
        settings, transport=httpx.MockTransport(handler)
    )

    result = await generate(settings, make_request("hello"))

    assert result.provider == "mock"
    assert "hello" in result.text


async def test_a_rejected_request_is_not_retried_elsewhere() -> None:
    """Vendor B will reject it too, and retrying just doubles the bill."""
    settings = make_settings(
        LLM_PROVIDER="gemini",
        LLM_API_KEY="test-key",
        LLM_FALLBACK_PROVIDER="mock",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={})

    from app.providers import registry

    registry._providers[f"gemini:{settings.llm_model}"] = GeminiProvider(
        settings, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(ProviderError) as raised:
        await generate(settings, make_request())

    assert raised.value.code == "provider_rejected"


async def test_without_a_fallback_the_failure_propagates() -> None:
    settings = make_settings(LLM_PROVIDER="gemini", LLM_API_KEY="test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    from app.providers import registry

    registry._providers[f"gemini:{settings.llm_model}"] = GeminiProvider(
        settings, transport=httpx.MockTransport(handler)
    )

    with pytest.raises(ProviderError) as raised:
        await generate(settings, make_request())

    assert raised.value.code == "provider_unavailable"


async def test_gemini_requires_an_api_key() -> None:
    with pytest.raises(RuntimeError, match="LLM_API_KEY"):
        GeminiProvider(make_settings(LLM_PROVIDER="gemini"))
