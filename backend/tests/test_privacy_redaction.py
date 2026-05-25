from __future__ import annotations

import pytest

from conftest import import_first, load_json_fixture, require_attr


PRIVACY_MODULES = (
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
