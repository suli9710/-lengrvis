from __future__ import annotations

import asyncio

import pytest

from app.config import AppSettings
from app.context_management import (
    ContextAwareProvider,
    PromptTooLongError,
    auto_compact_threshold,
    count_messages_tokens,
    effective_context_window,
    project_messages_for_llm,
    warning_state,
)
from app.llm.base import LLMProvider


def _settings(**overrides) -> AppSettings:
    settings = AppSettings(
        model_context_window=2000,
        model_auto_compact_token_limit=600,
        max_tokens=200,
        context_recent_message_limit=4,
        context_history_snip_threshold=12,
        context_history_snip_keep_recent=6,
        context_micro_compact_age=2,
        context_micro_compact_tool_result_chars=40,
        context_session_summary_limit=1000,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_context_thresholds_reserve_output_tokens():
    settings = _settings(model_context_window=2000, model_auto_compact_token_limit=0, max_tokens=250)

    assert effective_context_window(settings) == 1750
    assert auto_compact_threshold(settings) == 1050


def test_warning_state_uses_configured_auto_compact_limit():
    settings = _settings(model_auto_compact_token_limit=500)
    state = warning_state(520, settings)

    assert state.is_above_auto_compact_threshold is True
    assert state.percent_left == 0


def test_project_messages_microcompacts_old_tool_results():
    messages = [
        {"role": "user", "content": "read a file"},
        {"role": "tool", "content": "x" * 200, "tool_call_id": "tool_1"},
        {"role": "assistant", "content": "recent assistant"},
        {"role": "user", "content": "recent user"},
    ]

    projection = project_messages_for_llm(messages, _settings(), source="test")

    assert projection.micro_compacted is True
    assert projection.messages[1]["metadata"]["micro_compacted"] is True
    assert len(projection.messages[1]["content"]) < 200


def test_project_messages_snips_long_history_without_deleting_recent_tail():
    messages = [{"role": "user", "content": f"message {index}"} for index in range(20)]

    projection = project_messages_for_llm(messages, _settings(), source="test")

    assert projection.history_snipped is True
    assert len(projection.messages) < len(messages)
    assert projection.messages[-1]["content"] == "message 19"
    assert any("history snip" in message["content"].lower() for message in projection.messages)


def test_project_messages_auto_compacts_when_over_threshold():
    messages = [{"role": "user", "content": "x" * 1000} for _ in range(10)]

    projection = project_messages_for_llm(
        messages,
        _settings(context_history_snip_enabled=False, context_micro_compact_enabled=False),
        session_context={"current_workflow_state": {"phase": "testing"}},
        source="test",
    )

    assert projection.compacted is True
    assert projection.projected_tokens < projection.original_tokens
    assert any("auto-compaction" in message["content"].lower() for message in projection.messages)


def test_context_aware_provider_compacts_before_chat():
    class CapturingProvider(LLMProvider):
        name = "capture"

        def __init__(self):
            self.messages = []

        async def chat(self, messages, model=None, temperature=None, tools=None):  # noqa: ANN001, ARG002
            self.messages = messages
            return "ok"

        async def structured_chat(self, messages, output_schema):  # noqa: ANN001, ARG002
            self.messages = messages
            return {"ok": True}

    provider = CapturingProvider()
    wrapped = ContextAwareProvider(
        provider,
        _settings(context_history_snip_enabled=False, context_micro_compact_enabled=False),
    )
    messages = [{"role": "user", "content": "x" * 1000} for _ in range(10)]

    assert asyncio.run(wrapped.chat(messages)) == "ok"

    assert count_messages_tokens(provider.messages) < count_messages_tokens(messages)


def test_context_aware_provider_reactive_compacts_after_prompt_too_long():
    class FailingOnceProvider(LLMProvider):
        name = "failing"

        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, model=None, temperature=None, tools=None):  # noqa: ANN001, ARG002
            self.calls += 1
            self.messages = messages
            if self.calls == 1:
                raise PromptTooLongError("context_length_exceeded")
            return "ok"

        async def structured_chat(self, messages, output_schema):  # noqa: ANN001, ARG002
            return {"ok": True}

    provider = FailingOnceProvider()
    wrapped = ContextAwareProvider(provider, _settings())

    assert asyncio.run(wrapped.chat([{"role": "user", "content": "x" * 1000} for _ in range(10)])) == "ok"
    assert provider.calls == 2
    assert any("reactive" in message["content"].lower() or "auto-compaction" in message["content"].lower() for message in provider.messages)
