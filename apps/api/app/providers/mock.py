"""In-process mock model.

Development and CI must never need a vendor account, a key, or a billable request. This
answers deterministically from the request so tests can assert on the reply, and it emits
several deltas rather than one so the streaming path is genuinely exercised.

Two trigger phrases exist for testing failure handling, because "what happens when the
provider dies mid-generation" is a property the pending/failed message design exists to
answer and would otherwise never be tested:

    __provider_error__   raises ProviderError
    __empty__            returns an empty reply
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.core.errors import ProviderError
from app.providers.base import Delta, GenerationRequest

FAIL_TRIGGER = "__provider_error__"
EMPTY_TRIGGER = "__empty__"

# Rough enough for a mock. Real token accounting comes from the vendor's usage metadata.
_CHARS_PER_TOKEN = 4


class MockProvider:
    name = "mock"

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self._delay_seconds = delay_seconds

    async def stream(self, request: GenerationRequest) -> AsyncIterator[Delta]:
        prompt = request.turns[-1].content if request.turns else ""

        if FAIL_TRIGGER in prompt:
            raise ProviderError("The mock provider was asked to fail.", code="provider_error")

        reply = "" if EMPTY_TRIGGER in prompt else self._compose(request, prompt)

        for word in reply.split(" "):
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            yield Delta(text=word + " ")

        yield Delta(
            finish_reason="stop",
            model=request.model,
            input_tokens=self._estimate(request),
            output_tokens=max(1, len(reply) // _CHARS_PER_TOKEN),
        )

    def _compose(self, request: GenerationRequest, prompt: str) -> str:
        # Echoing the prompt back is what makes assertions possible without pinning the
        # test to a fixed string that says nothing about whether the turn arrived intact.
        return (
            f"[mock:{request.model}] I heard: {prompt.strip()}. "
            f"This conversation has {len(request.turns)} turns so far."
        )

    def _estimate(self, request: GenerationRequest) -> int:
        characters = len(request.system) + sum(len(turn.content) for turn in request.turns)
        return max(1, characters // _CHARS_PER_TOKEN)
