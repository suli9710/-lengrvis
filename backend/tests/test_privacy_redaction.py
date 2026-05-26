from __future__ import annotations

import pytest

from conftest import import_first, load_json_fixture, require_attr


PRIVACY_MODULES = (
    "app.policy.redaction",
    "backend.privacy.redaction",
    "backend.security.redaction",
    "backend.core.privacy",
    "mavris.privacy.redaction",
)


@pytest.fixture
def redact():
    module = import_first(PRIVACY_MODULES)
    return require_attr(module, ("redact_text", "redact", "redact_payload", "sanitize_text"))


def _call_redact(redact, payload):
    try:
        return redact(payload)
    except TypeError:
        return redact(text=payload)


def test_redacts_common_secret_and_pii_patterns(redact):
    sample = load_json_fixture("privacy/pii_payload.json")
    output = _call_redact(redact, sample["message"])
    text = str(output)

    assert "alice@example.com" not in text
    assert "555-0199" not in text
    assert "sk-test-1234567890abcdef" not in text
    assert "[REDACTED" in text or "***" in text


def test_preserves_non_sensitive_context(redact):
    output = str(_call_redact(redact, "Schedule the workspace index refresh after lunch."))

    assert "workspace index refresh" in output
    assert "lunch" in output


def test_redacts_nested_headers_urls_and_form_values():
    from app.core.audit import record
    from app.policy.redaction import REDACTED, redact_payload

    payload = {
        "headers": {
            "Authorization": "Bearer live-secret-token",
            "Cookie": "session=very-secret-cookie",
            "X-Trace": "trace-1",
        },
        "url": "https://example.com/callback?token=abc123456789&safe=1",
        "form": {
            "username": "Alice",
            "card_number": "4111111111111111",
            "notes": "opaque token abcdef1234567890",
        },
        "items": [{"api_key": "sk-test-1234567890abcdef"}],
    }

    redacted = redact_payload(payload)
    text = str(redacted)

    assert redacted["headers"]["Authorization"] == REDACTED
    assert redacted["headers"]["Cookie"] == REDACTED
    assert redacted["headers"]["X-Trace"] == "trace-1"
    assert "abc123456789" not in text
    assert "very-secret-cookie" not in text
    assert "4111111111111111" not in text
    assert "abcdef1234567890" not in text
    assert "username" in redacted["form"]

    event = record("test.redaction", "pytest", payload)
    event_text = str(event.payload)
    assert "live-secret-token" not in event_text
    assert "very-secret-cookie" not in event_text
