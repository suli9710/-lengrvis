from __future__ import annotations

from app.policy.browser_content import browser_content_warning_hits, has_browser_content_trust_label
from app.policy.policy_rules import BROWSER_CONTENT_PROMPT_INJECTION_WARNING, BROWSER_CONTENT_TRUST


def test_browser_content_warning_hits_collects_nested_warning_values():
    payload = {
        "result": [
            {"browser_content_warnings": [BROWSER_CONTENT_PROMPT_INJECTION_WARNING]},
            {"nested": {"browser_content_warnings": ("second_warning",)}},
        ],
        "metadata": {"other": "ignored"},
    }

    assert browser_content_warning_hits(payload) == {
        BROWSER_CONTENT_PROMPT_INJECTION_WARNING,
        "second_warning",
    }


def test_browser_content_warning_hits_accepts_scalar_warning_marker():
    assert browser_content_warning_hits({"browser_content_warnings": "scalar_warning"}) == {"scalar_warning"}


def test_has_browser_content_trust_label_finds_nested_exact_marker():
    payload = {
        "outer": [
            {"content_trust": "trusted"},
            {"inner": {"content_trust": BROWSER_CONTENT_TRUST}},
        ]
    }

    assert has_browser_content_trust_label(payload) is True


def test_has_browser_content_trust_label_requires_exact_key_and_value():
    payload = {
        "content_trust": "trusted",
        "other": f"content_trust={BROWSER_CONTENT_TRUST}",
        "nested": {"contentTrust": BROWSER_CONTENT_TRUST},
    }

    assert has_browser_content_trust_label(payload) is False
