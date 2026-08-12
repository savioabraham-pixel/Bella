"""Token estimation for context budgeting.

Deliberately an estimate. Exact counts would mean either shipping a tokenizer per vendor or
a `countTokens` round trip before every turn — one is a large dependency that goes stale
when the vendor changes its vocabulary, the other adds latency to the path the user is
waiting on.

What this feeds is a budget decision: how much history fits. Being 15% out moves the
boundary by a turn or two, which nobody notices. **Billing and usage reporting never use
this** — those come from the vendor's own `usageMetadata`, recorded on the message row.

The divisor is deliberately pessimistic for the four languages this household actually
speaks. English averages about four characters per token; Devanagari, Bengali and Marathi
fragment far more, so a single ratio tuned for English would under-count and overflow the
real context window.
"""

from __future__ import annotations

from app.providers.base import Turn

# Latin-ish text: ~4 characters per token.
_LATIN_CHARS_PER_TOKEN = 4.0
# Indic scripts fragment much harder — closer to 1.5 characters per token in practice.
_COMPLEX_CHARS_PER_TOKEN = 1.5

# Per-message overhead: role markers and the delimiters a vendor wraps each turn in.
_PER_TURN_OVERHEAD = 4


def _is_complex_script(character: str) -> bool:
    """Devanagari, Bengali, and the surrounding Indic blocks, plus CJK."""
    code = ord(character)
    return (
        0x0900 <= code <= 0x0DFF  # Devanagari through Sinhala
        or 0x3000 <= code <= 0x9FFF  # CJK
        or 0xAC00 <= code <= 0xD7AF  # Hangul
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0

    complex_chars = sum(1 for character in text if _is_complex_script(character))
    latin_chars = len(text) - complex_chars

    estimate = latin_chars / _LATIN_CHARS_PER_TOKEN + complex_chars / _COMPLEX_CHARS_PER_TOKEN
    return max(1, round(estimate))


def estimate_turn_tokens(turn: Turn) -> int:
    return estimate_tokens(turn.content) + _PER_TURN_OVERHEAD


def estimate_prompt_tokens(system: str, turns: list[Turn]) -> int:
    return estimate_tokens(system) + sum(estimate_turn_tokens(turn) for turn in turns)
