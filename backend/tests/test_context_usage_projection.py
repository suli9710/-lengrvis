from __future__ import annotations

from app.config import AppSettings
from app.context.usage_projection import projection_brief, projection_summary
from app.context_management import count_messages_tokens


def _settings(**overrides) -> AppSettings:
    settings = AppSettings(
        model_context_window=1000,
        model_auto_compact_token_limit=600,
        max_tokens=100,
        context_manual_compact_buffer_tokens=50,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_projection_summary_disabled_reports_identity_projection():
    messages = [{"role": "user", "content": "hello"}]

    projection = projection_summary(
        messages,
        _settings(),
        session_context={},
        include_projection=False,
    )

    assert projection["enabled"] is False
    assert projection["original_count"] == 1
    assert projection["projected_count"] == 1
    assert projection["projected_tokens"] == count_messages_tokens(messages)
    assert projection["summary"]["description"] == "Projection is disabled for this usage estimate."


def test_projection_brief_reports_savings_and_adjustments():
    brief = projection_brief(
        {
            "enabled": True,
            "original_count": "10",
            "projected_count": "4",
            "original_tokens": "1200",
            "projected_tokens": "500",
            "compacted": True,
            "micro_compacted": True,
            "history_snipped": True,
            "strategy": "micro+snip",
        }
    )

    assert brief["tokens_saved"] == 700
    assert brief["messages_removed"] == 6
    assert brief["adjustments"] == ["micro_compacted", "history_snipped"]
    assert brief["description"] == "Projection trims context before the provider call."


def test_projection_brief_clamps_negative_savings_and_invalid_counts():
    brief = projection_brief(
        {
            "enabled": True,
            "original_count": "bad",
            "projected_count": 3,
            "original_tokens": 100,
            "projected_tokens": 200,
            "session_summary_added": True,
            "strategy": "",
        }
    )

    assert brief["tokens_saved"] == 0
    assert brief["messages_removed"] == 0
    assert brief["strategy"] == "none"
    assert brief["description"] == "Projection adds session continuity context."
