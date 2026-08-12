"""The provider contract.

Written streaming-first: `stream()` is the primitive and `generate()` is its aggregation.
SSE has not landed yet, but designing the interface around whole responses would mean
rewriting every adapter when it does — and a provider that can only return whole responses
cannot report time-to-first-token, which is the latency number that actually matters to
someone waiting for Bella to start talking.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

TurnRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class Turn:
    """One exchange in the conversation as the model should see it.

    Deliberately not the `Message` ORM object: the provider layer must not depend on the
    database schema, and half of `Message` is bookkeeping the model has no business seeing.
    """

    role: TurnRole
    content: str


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    system: str
    turns: list[Turn]
    model: str
    max_output_tokens: int
    temperature: float


@dataclass(slots=True)
class GenerationResult:
    text: str
    model: str
    provider: str
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    first_token_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Delta:
    """An incremental piece of the reply."""

    text: str = ""
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def stream(self, request: GenerationRequest) -> AsyncIterator[Delta]: ...


@dataclass(slots=True)
class ResultAccumulator:
    """Collects deltas into a result, timing the first one separately.

    Public because the streaming path needs to fan each delta out to Redis as it arrives
    while still ending up with the same aggregate `collect()` produces.
    """

    started: float = field(default_factory=time.perf_counter)
    chunks: list[str] = field(default_factory=list)
    first_token_ms: int | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str | None = None

    def add(self, delta: Delta) -> None:
        if delta.text:
            if self.first_token_ms is None:
                self.first_token_ms = int((time.perf_counter() - self.started) * 1000)
            self.chunks.append(delta.text)
        # Usage and finish reason arrive on the final delta for most vendors, but nothing
        # in the contract says they cannot arrive earlier, so last-write-wins.
        if delta.finish_reason is not None:
            self.finish_reason = delta.finish_reason
        if delta.input_tokens is not None:
            self.input_tokens = delta.input_tokens
        if delta.output_tokens is not None:
            self.output_tokens = delta.output_tokens
        if delta.model is not None:
            self.model = delta.model

    def finish(self, default_model: str, provider: str) -> GenerationResult:
        return GenerationResult(
            text="".join(self.chunks),
            model=self.model or default_model,
            provider=provider,
            finish_reason=self.finish_reason,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            latency_ms=int((time.perf_counter() - self.started) * 1000),
            first_token_ms=self.first_token_ms,
        )


async def collect(provider: LLMProvider, request: GenerationRequest) -> GenerationResult:
    """Drain a stream into a single result, for callers with no reader to feed."""
    accumulator = ResultAccumulator()
    async for delta in provider.stream(request):
        accumulator.add(delta)
    return accumulator.finish(request.model, provider.name)
