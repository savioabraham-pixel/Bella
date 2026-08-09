"""Log redaction.

Message content, memory content, emails, phone numbers and tokens must never reach a log
sink. This is enforced by a processor in the pipeline rather than at each call site,
precisely so that a future call site cannot get it wrong — these tests hold that line.
"""

from __future__ import annotations

from app.core.logging import REDACTED, redact_processor


def _process(**event: object) -> dict[str, object]:
    return dict(redact_processor(None, "info", dict(event)))


def test_message_content_is_dropped() -> None:
    result = _process(event="chat_turn", content="my father passed away last year")

    assert result["content"] == REDACTED


def test_memory_content_is_dropped() -> None:
    result = _process(event="memory_write", memory="recent eye surgery")

    assert result["memory"] == REDACTED


def test_tokens_and_credentials_are_dropped() -> None:
    result = _process(
        event="auth",
        access_token="eyJhbGciOi.abc",
        refresh_token="opaque",
        password="hunter2",
        api_key="sk-live-123",
    )

    assert all(
        result[key] == REDACTED for key in ("access_token", "refresh_token", "password", "api_key")
    )


def test_email_addresses_are_scrubbed_from_free_text() -> None:
    result = _process(event="signup_failed", reason="could not reach alice@example.com")

    assert "alice@example.com" not in str(result["reason"])
    assert "[email]" in str(result["reason"])


def test_phone_numbers_are_scrubbed_from_free_text() -> None:
    result = _process(event="otp_failed", reason="no route to +91 98765 43210")

    assert "98765" not in str(result["reason"])
    assert "[phone]" in str(result["reason"])


def test_bearer_tokens_are_scrubbed_from_free_text() -> None:
    result = _process(event="proxy", reason="upstream rejected Bearer eyJhbGciOiJIUzI1")

    assert "eyJhbGciOiJIUzI1" not in str(result["reason"])


def test_ordinary_fields_survive() -> None:
    """Redaction must not make logs useless."""
    result = _process(event="http_request", status_code=200, duration_ms=12.5)

    assert result["status_code"] == 200
    assert result["duration_ms"] == 12.5


def test_iso_timestamps_survive() -> None:
    """Regression: the phone matcher used to eat every log timestamp.

    `2026-08-09T06:58:16Z` matched the old digits-and-separators pattern, so production
    logs shipped with `"timestamp": "[phone]T06:58:16Z"`.
    """
    result = _process(event="startup", timestamp="2026-08-09T06:58:16.401627Z")

    assert result["timestamp"] == "2026-08-09T06:58:16.401627Z"


def test_dates_in_free_text_survive() -> None:
    result = _process(event="report", reason="covering 2026-08-09 to 2026-08-16")

    assert "2026-08-09" in str(result["reason"])


def test_large_numbers_are_not_mistaken_for_phone_numbers() -> None:
    result = _process(event="upload", reason="rejected 104857600 bytes over the limit")

    assert "104857600" in str(result["reason"])


def test_structural_fields_are_left_alone() -> None:
    result = _process(event="http_request", path="/v1/threads/12345678", status_code=200)

    assert result["path"] == "/v1/threads/12345678"


def test_bare_ten_digit_number_is_still_treated_as_a_phone() -> None:
    result = _process(event="otp", reason="tried 9876543210 twice")

    assert "9876543210" not in str(result["reason"])
