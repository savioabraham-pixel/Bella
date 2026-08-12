"""Outbound model access.

Everything the application knows about a vendor lives behind `LLMProvider`. Swapping the
incumbent, adding a fallback, or running the whole stack against a mock is a configuration
change, not a code change — which is the point, because the legacy system had the vendor's
request shape spread through a 5,540-line HTML file.
"""

from app.providers.base import (
    GenerationRequest,
    GenerationResult,
    LLMProvider,
    Turn,
)
from app.providers.registry import get_llm_provider, reset_providers

__all__ = [
    "GenerationRequest",
    "GenerationResult",
    "LLMProvider",
    "Turn",
    "get_llm_provider",
    "reset_providers",
]
