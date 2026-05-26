from __future__ import annotations

import asyncio

import pytest

from app.config import AppSettings
from app.core import db
from app.context_management import ContextAwareProvider, LLMCapabilityError
from app.llm.base import LLMProvider
from app.llm.profiles import profile_for_settings
from app.llm.usage import list_usage_events, usage_summary
from app.llm.types import LLMResponse, LLMUsage


class ResultProvider(LLMProvider):
    name = "result"

    async def chat(self, messages, model=None, temperature=None, tools=None):  # noqa: ANN001, ARG002
        return "ok"

    async def chat_result(self, messages, model=None, temperature=None, tools=None):  # noqa: ANN001, ARG002
        return LLMResponse(
            content="ok",
            provider=self.name,
            model=model or "gpt-4o-mini",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, estimated=False),
        )

    async def structured_chat(self, messages, output_schema):  # noqa: ANN001, ARG002
        return {"ok": True}


def test_profile_uses_configured_context_for_unknown_model():
    settings = AppSettings(provider_name="openai", model="unknown-model", model_context_window=7777, max_tokens=321)

    profile = profile_for_settings(settings)

    assert profile.model_profile.context_window == 7777
    assert profile.model_profile.max_output_tokens == 321
    assert profile.model_profile.known is False
    assert profile.capabilities.prompt_cache is False


def test_context_provider_rejects_tools_when_profile_does_not_support_them():
    settings = AppSettings(provider_name="mock", model="mock")
    profile = profile_for_settings(settings, provider_name="mock", model="mock")
    wrapped = ContextAwareProvider(ResultProvider(), settings, profile=profile)

    with pytest.raises(LLMCapabilityError):
        asyncio.run(wrapped.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}]))


def test_context_provider_adds_cost_to_chat_result(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    settings = AppSettings(
        provider_name="openai",
        model="gpt-4o-mini",
        mode="efficiency",
        data_dir=str(tmp_path),
        context_auto_compact_enabled=False,
        context_micro_compact_enabled=False,
        context_history_snip_enabled=False,
        context_session_memory_enabled=False,
    )
    profile = profile_for_settings(settings, provider_name="openai", model="gpt-4o-mini")
    wrapped = ContextAwareProvider(ResultProvider(), settings, profile=profile)

    response = asyncio.run(wrapped.chat_result([{"role": "user", "content": "hi"}]))

    assert response.content == "ok"
    assert response.cost is not None
    assert response.cost.total_cost_usd is not None


def test_context_provider_records_structured_chat_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    settings = AppSettings(
        provider_name="openai",
        model="gpt-4o-mini",
        mode="efficiency",
        data_dir=str(tmp_path),
        context_auto_compact_enabled=False,
        context_micro_compact_enabled=False,
        context_history_snip_enabled=False,
        context_session_memory_enabled=False,
    )
    db.init_db()
    profile = profile_for_settings(settings, provider_name="openai", model="gpt-4o-mini")
    wrapped = ContextAwareProvider(ResultProvider(), settings, profile=profile)

    payload = asyncio.run(
        wrapped.structured_chat(
            [{"role": "user", "content": "return json"}],
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )
    )

    events = list_usage_events(limit=5)
    structured = next(event for event in events if event["purpose"] == "structured_chat")
    assert payload == {"ok": True}
    assert structured["usage_breakdown"]["input_tokens"] > 0
    assert structured["claude_usage"]["output_tokens"] > 0
    assert structured["projection"]["source"].endswith(":structured")


def test_usage_summary_reads_stored_claude_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    settings = AppSettings(
        provider_name="openai",
        model="gpt-4o-mini",
        mode="efficiency",
        data_dir=str(tmp_path),
        context_auto_compact_enabled=False,
        context_micro_compact_enabled=False,
        context_history_snip_enabled=False,
        context_session_memory_enabled=False,
    )
    db.init_db()
    profile = profile_for_settings(settings, provider_name="openai", model="gpt-4o-mini")
    wrapped = ContextAwareProvider(ResultProvider(), settings, profile=profile)

    asyncio.run(wrapped.chat_result([{"role": "user", "content": "hi"}]))

    summary = usage_summary(hours=1)
    assert summary["calls"] == 1
    assert "claude_usage" in summary
    assert summary["claude_usage"]["input_tokens"] >= 0
