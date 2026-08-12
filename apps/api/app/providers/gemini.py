"""Gemini adapter.

The incumbent, carried over from the legacy Cloud Run backend so the persona does not
change in the same week as the platform. It speaks the REST API directly over `httpx`
rather than through the vendor SDK: the SDK's async story is thinner than its sync one, and
one JSON shape is not worth a dependency tree.

Two differences from the legacy client this replaces:

* the API key never reaches a browser — the whole reason the gateway is server-side;
* the system prompt goes in `system_instruction` rather than being smuggled in as a fake
  first user turn followed by a fake model acknowledgement, which is what `index.html`
  did and which spends tokens on every request to say "Understood. I am Bella."
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.providers.base import Delta, GenerationRequest

logger = get_logger(__name__)

# Gemini calls the assistant "model"; everything else in this codebase calls it "assistant".
_ROLE_MAP = {"user": "user", "assistant": "model"}

_SSE_DATA_PREFIX = "data: "
_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        settings: Settings,
        model: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if settings.llm_api_key is None:
            raise RuntimeError("LLM_API_KEY is required when LLM_PROVIDER=gemini.")
        self._settings = settings
        self._model = model or settings.llm_model
        self._api_key = settings.llm_api_key.get_secret_value()
        # Injected only by tests. The SSE parser and the error mapping are real logic and
        # would otherwise be verifiable only by calling a paid API.
        self._transport = transport

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        return {
            "system_instruction": {"parts": [{"text": request.system}]},
            "contents": [
                {"role": _ROLE_MAP[turn.role], "parts": [{"text": turn.content}]}
                for turn in request.turns
            ],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }

    async def stream(self, request: GenerationRequest) -> AsyncIterator[Delta]:
        url = (
            f"{self._settings.gemini_api_base}/models/{request.model}:streamGenerateContent?alt=sse"
        )
        headers = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }

        try:
            async with (
                httpx.AsyncClient(
                    timeout=self._settings.llm_timeout_seconds, transport=self._transport
                ) as client,
                client.stream(
                    "POST", url, headers=headers, json=self._payload(request)
                ) as response,
            ):
                if response.status_code >= 400:
                    await response.aread()
                    raise self._error_for(response)

                async for line in response.aiter_lines():
                    if not line.startswith(_SSE_DATA_PREFIX):
                        continue
                    delta = self._parse(line.removeprefix(_SSE_DATA_PREFIX))
                    if delta is not None:
                        yield delta

        except httpx.TimeoutException as exc:
            raise ProviderError(
                "The model took too long to answer.", code="provider_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("gemini_transport_error", error_type=type(exc).__name__)
            raise ProviderError("Could not reach the model.", code="provider_unreachable") from exc

    def _error_for(self, response: httpx.Response) -> ProviderError:
        retryable = response.status_code in _RETRYABLE_STATUSES
        logger.warning("gemini_http_error", status_code=response.status_code, retryable=retryable)
        # The vendor's error body can quote the prompt back, which would put message
        # content into a log line. Only the status code is recorded.
        return ProviderError(
            "The model is unavailable right now."
            if retryable
            else "The model rejected that request.",
            code="provider_unavailable" if retryable else "provider_rejected",
            details={"retryable": retryable},
        )

    def _parse(self, raw: str) -> Delta | None:
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("gemini_unparseable_chunk")
            return None

        text = ""
        finish_reason = None
        for candidate in chunk.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                text += part.get("text") or ""
            finish_reason = candidate.get("finishReason") or finish_reason

        usage = chunk.get("usageMetadata") or {}
        return Delta(
            text=text,
            finish_reason=finish_reason.lower() if finish_reason else None,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            model=chunk.get("modelVersion"),
        )
