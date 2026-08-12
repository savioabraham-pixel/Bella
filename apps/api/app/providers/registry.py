"""Provider construction and the fallback chain.

The fallback exists because a single vendor's bad afternoon should not be Bella going
silent for the family. It is deliberately narrow: a fallback is attempted only for failures
that are plausibly transient and vendor-specific. A rejected request will be rejected by the
second vendor too, and retrying it just doubles the bill.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

from app.core.config import Settings
from app.core.errors import ProviderError
from app.core.logging import get_logger
from app.providers.base import (
    Delta,
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    collect,
)
from app.providers.gemini import GeminiProvider
from app.providers.mock import MockProvider

logger = get_logger(__name__)

# Failures worth trying a second vendor for. Anything else — a malformed request, a safety
# refusal — will fail identically downstream.
_FAILOVER_CODES = frozenset({"provider_timeout", "provider_unreachable", "provider_unavailable"})

_providers: dict[str, LLMProvider] = {}


def _build(settings: Settings, name: str, model: str | None) -> LLMProvider:
    if name == "gemini":
        return GeminiProvider(settings, model)
    return MockProvider()


def get_llm_provider(settings: Settings) -> LLMProvider:
    """The incumbent. One instance per process, so connection pools are reused."""
    key = f"{settings.llm_provider}:{settings.llm_model}"
    if key not in _providers:
        _providers[key] = _build(settings, settings.llm_provider, settings.llm_model)
        logger.info("llm_provider_ready", provider=settings.llm_provider)
    return _providers[key]


def get_fallback_provider(settings: Settings) -> tuple[LLMProvider, str] | None:
    if settings.llm_fallback_provider is None:
        return None

    model = settings.llm_fallback_model or settings.llm_model
    key = f"{settings.llm_fallback_provider}:{model}"
    if key not in _providers:
        _providers[key] = _build(settings, settings.llm_fallback_provider, model)
    return _providers[key], model


async def generate(settings: Settings, request: GenerationRequest) -> GenerationResult:
    """Run a request against the incumbent, falling back only where it can help."""
    provider = get_llm_provider(settings)
    try:
        return await collect(provider, request)
    except ProviderError as exc:
        fallback = get_fallback_provider(settings)
        if fallback is None or exc.code not in _FAILOVER_CODES:
            raise

        second, model = fallback
        logger.warning("llm_failover", primary=provider.name, fallback=second.name, reason=exc.code)
        # The fallback may be a different model, so the request is rebuilt rather than
        # reused — asking vendor B for vendor A's model name is a guaranteed rejection.
        return await collect(second, replace(request, model=model))


async def stream(settings: Settings, request: GenerationRequest) -> AsyncIterator[Delta]:
    """Stream deltas, falling back only before the first one has been emitted.

    Once a token has reached the user, switching vendors mid-reply would splice two
    different answers together. After that point a failure is a failure — the turn is
    marked failed and stays retryable, which is a better outcome than a sentence that
    changes its mind halfway through.
    """
    provider = get_llm_provider(settings)
    emitted = False

    try:
        async for delta in provider.stream(request):
            emitted = emitted or bool(delta.text)
            yield delta
        return
    except ProviderError as exc:
        fallback = get_fallback_provider(settings)
        if emitted or fallback is None or exc.code not in _FAILOVER_CODES:
            raise

        second, model = fallback
        logger.warning("llm_failover", primary=provider.name, fallback=second.name, reason=exc.code)

    second, model = fallback
    async for delta in second.stream(replace(request, model=model)):
        yield delta


def reset_providers() -> None:
    """Test hook — drops cached instances so settings can change between cases."""
    _providers.clear()
