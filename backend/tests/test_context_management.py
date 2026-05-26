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
    parse_prompt_too_long_token_counts,
    project_ledger_for_llm,
    project_messages_for_llm,
    recent_complete_tail_start,
    repair_tool_message_invariants,
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


def test_prompt_too_long_error_carries_token_gap():
    error = PromptTooLongError(
        "prompt is too long: 137500 tokens > 135000 maximum",
        actual_tokens=137500,
        limit_tokens=135000,
    )

    assert error.token_gap == 2500
    assert error.to_dict()["actual_tokens"] == 137500
    assert parse_prompt_too_long_token_counts(str(error)) == (137500, 135000)


def test_project_messages_microcompacts_old_tool_results():
    messages = [
        {"role": "user", "content": "read a file"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tool_1",
                    "type": "function",
                    "function": {"name": "file.read", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "content": "x" * 200, "tool_call_id": "tool_1"},
        {"role": "assistant", "content": "recent assistant"},
        {"role": "user", "content": "recent user"},
    ]

    projection = project_messages_for_llm(messages, _settings(), source="test")

    assert projection.micro_compacted is True
    tool_message = next(message for message in projection.messages if message.get("role") == "tool")
    assert tool_message["metadata"]["micro_compacted"] is True
    assert len(tool_message["content"]) < 200


def test_microcompact_records_boundary_metadata_and_tool_summary():
    messages = [
        {"id": "u1", "role": "user", "content": "run a search"},
        {
            "id": "a1",
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tool_search",
                    "type": "function",
                    "function": {"name": "search.query", "arguments": {"query": "mavris context compaction"}},
                }
            ],
        },
        {"id": "t1", "role": "tool", "content": "result row\n" * 80, "tool_call_id": "tool_search"},
        {"id": "recent", "role": "user", "content": "recent"},
    ]

    projection = project_messages_for_llm(
        messages,
        _settings(
            context_micro_compact_age=1,
            context_micro_compact_tool_result_chars=240,
            context_history_snip_enabled=False,
            context_auto_compact_enabled=False,
        ),
        source="test",
    )

    assert projection.micro_compacted is True
    assert projection.compact_metadata["tokens_saved"] > 0
    assert projection.compact_metadata["compacted_tool_ids"] == ["tool_search"]
    assert projection.compact_metadata["micro_compact"]["collapsed_tool_results"][0]["kind"] == "search"
    tool_message = next(message for message in projection.messages if message.get("role") == "tool")
    assert "Tool result collapsed for projection" in tool_message["content"]
    assert "query: mavris context compaction" in tool_message["content"]


def test_microcompact_clears_attachment_blocks_in_projection_only():
    messages = [
        {"id": "u1", "role": "user", "content": "see image"},
        {
            "id": "u2",
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect"},
                {"type": "image_url", "id": "att_1", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        },
        {"id": "recent", "role": "assistant", "content": "ok"},
    ]

    projection = project_messages_for_llm(
        messages,
        _settings(context_micro_compact_age=1, context_history_snip_enabled=False, context_auto_compact_enabled=False),
        source="test",
    )

    assert projection.compact_metadata["cleared_attachment_ids"] == ["att_1"]
    assert messages[1]["content"][1]["type"] == "image_url"
    projected = next(message for message in projection.messages if message.get("id") == "u2")
    assert projected["content"][1]["type"] == "text"


def test_project_messages_snips_long_history_without_deleting_recent_tail():
    messages = [{"role": "user", "content": f"message {index}"} for index in range(20)]

    projection = project_messages_for_llm(messages, _settings(), source="test")

    assert projection.history_snipped is True
    assert len(projection.messages) < len(messages)
    assert projection.messages[-1]["content"] == "message 19"
    assert any("history snip" in message["content"].lower() for message in projection.messages)


def test_project_ledger_for_llm_uses_latest_boundary_and_preserved_segment():
    older_boundary = {
        "id": "boundary_old",
        "role": "system",
        "content": "old compact summary",
        "metadata": {"context_boundary": "manual_compact", "compact_boundary": True},
    }
    call_owner = {
        "id": "call_owner",
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_preserved",
                "type": "function",
                "function": {"name": "system.get_info", "arguments": "{}"},
            }
        ],
    }
    call_result = {
        "id": "call_result",
        "role": "tool",
        "tool_call_id": "call_preserved",
        "content": "preserved tool output",
    }
    latest_boundary = {
        "id": "boundary_new",
        "role": "system",
        "content": "new compact summary",
        "metadata": {
            "compact_metadata": {
                "type": "manual_compact",
                "preserved_segment": [call_result],
                "preserved_message_ids": ["recent_before_boundary"],
            }
        },
    }
    ledger = [
        {"id": "system_1", "role": "system", "content": "policy"},
        older_boundary,
        {"id": "old_history", "role": "user", "content": "hidden by latest boundary"},
        call_owner,
        {"id": "recent_before_boundary", "role": "user", "content": "keep me"},
        latest_boundary,
        {"id": "after_boundary", "role": "user", "content": "new work"},
    ]

    projection = project_ledger_for_llm(
        ledger,
        _settings(
            context_history_snip_enabled=False,
            context_micro_compact_enabled=False,
            context_auto_compact_enabled=False,
        ),
        source="test-ledger",
    )
    contents = [message.get("content") for message in projection.messages]

    assert "hidden by latest boundary" not in contents
    assert "old compact summary" not in contents
    assert projection.boundary_id == "boundary_new"
    assert projection.compact_metadata["type"] == "manual_compact"
    assert "keep me" in contents
    assert any(
        any(tool_call.get("id") == "call_preserved" for tool_call in message.get("tool_calls") or [])
        for message in projection.messages
    )
    assert any(message.get("tool_call_id") == "call_preserved" for message in projection.messages)


def test_project_ledger_filters_old_boundary_from_preserved_tail():
    old_boundary = {
        "id": "boundary_old",
        "role": "system",
        "content": "old compact summary",
        "metadata": {"context_boundary": "manual_compact", "compact_boundary": True},
    }
    new_boundary = {
        "id": "boundary_new",
        "role": "system",
        "content": "new compact summary",
        "metadata": {
            "compact_metadata": {
                "type": "manual_compact",
                "messages_to_keep_ids": ["boundary_old", "recent_tail"],
                "preserved_segment": {"message_ids": ["recent_tail"]},
            }
        },
    }
    projection = project_ledger_for_llm(
        [
            {"id": "sys", "role": "system", "content": "policy"},
            old_boundary,
            {"id": "recent_tail", "role": "user", "content": "tail"},
            new_boundary,
            {"id": "after", "role": "user", "content": "after"},
        ],
        _settings(context_history_snip_enabled=False, context_micro_compact_enabled=False, context_auto_compact_enabled=False),
        source="test-ledger",
    )

    assert projection.boundary_id == "boundary_new"
    assert [message.get("id") for message in projection.messages].count("boundary_old") == 0
    assert any(message.get("id") == "recent_tail" for message in projection.messages)


def test_history_snip_keeps_tool_call_pair_when_tail_starts_on_tool_result():
    messages = [{"role": "user", "content": f"old {index}"} for index in range(10)]
    messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "system.get_info", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "tool output"},
            {"role": "assistant", "content": "done"},
        ]
    )

    projection = project_messages_for_llm(
        messages,
        _settings(context_history_snip_threshold=8, context_history_snip_keep_recent=2, context_auto_compact_enabled=False),
        source="test",
    )

    assert recent_complete_tail_start(messages, 2) == len(messages) - 3
    assert any(message.get("tool_calls") for message in projection.messages)
    assert any(message.get("tool_call_id") == "call_1" for message in projection.messages)


def test_repair_tool_invariants_demotes_orphan_tool_result_without_dropping_content():
    repaired = repair_tool_message_invariants(
        [
            {"role": "tool", "tool_call_id": "missing_call", "content": "important observation"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "no_result",
                        "type": "function",
                        "function": {"name": "file.read", "arguments": "{}"},
                    }
                ],
            },
        ]
    )

    assert repaired[0]["role"] == "assistant"
    assert repaired[0]["content"] == "important observation"
    assert repaired[0]["metadata"]["orphan_tool_result_compacted"] is True
    assert "tool_calls" not in repaired[1]
    assert repaired[1]["metadata"]["tool_calls_compacted"] is True


def test_repair_tool_invariants_drops_malformed_tool_calls():
    repaired = repair_tool_message_invariants(
        [
            {"role": "assistant", "content": "", "tool_calls": [{"type": "function", "function": {"name": "bad"}}]},
        ]
    )

    assert "tool_calls" not in repaired[0]
    assert repaired[0]["metadata"]["tool_calls_compacted"] is True


def test_repair_tool_invariants_requires_contiguous_tool_pair():
    repaired = repair_tool_message_invariants(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_late",
                        "type": "function",
                        "function": {"name": "system.get_info", "arguments": "{}"},
                    }
                ],
            },
            {"role": "assistant", "content": "interleaving message"},
            {"role": "tool", "tool_call_id": "call_late", "content": "late result"},
        ]
    )

    assert "tool_calls" not in repaired[0]
    assert repaired[0]["metadata"]["tool_calls_compacted"] is True
    assert repaired[1]["content"] == "interleaving message"
    assert repaired[2]["role"] == "assistant"
    assert repaired[2]["content"] == "late result"
    assert repaired[2]["metadata"]["orphan_tool_result_compacted"] is True


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


def test_auto_compact_keeps_tool_call_pair_when_tail_starts_on_tool_result():
    messages = [{"role": "user", "content": "x" * 1000} for _ in range(6)]
    messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_auto",
                        "type": "function",
                        "function": {"name": "system.get_info", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_auto", "content": "auto tool output"},
            {"role": "assistant", "content": "after auto tool"},
        ]
    )

    projection = project_messages_for_llm(
        messages,
        _settings(
            context_history_snip_enabled=False,
            context_micro_compact_enabled=False,
            context_recent_message_limit=2,
        ),
        source="test",
    )

    assert projection.compacted is True
    assert any(
        any(tool_call.get("id") == "call_auto" for tool_call in message.get("tool_calls") or [])
        for message in projection.messages
    )
    assert any(message.get("tool_call_id") == "call_auto" for message in projection.messages)


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


def test_reactive_compact_keeps_tool_call_pair_when_tail_starts_on_tool_result():
    class FailingProvider(LLMProvider):
        name = "reactive_tail"

        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, model=None, temperature=None, tools=None):  # noqa: ANN001, ARG002
            self.calls += 1
            self.messages = messages
            if self.calls == 1:
                raise PromptTooLongError("prompt too long")
            return "ok"

        async def structured_chat(self, messages, output_schema):  # noqa: ANN001, ARG002
            return {"ok": True}

    provider = FailingProvider()
    wrapped = ContextAwareProvider(
        provider,
        _settings(
            model_auto_compact_token_limit=100000,
            context_history_snip_enabled=False,
            context_micro_compact_enabled=False,
            context_recent_message_limit=4,
        ),
    )
    messages = [{"role": "user", "content": f"old {index}"} for index in range(8)]
    messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_reactive",
                        "type": "function",
                        "function": {"name": "system.get_info", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_reactive", "content": "reactive tool output"},
            {"role": "assistant", "content": "after reactive tool"},
        ]
    )

    assert asyncio.run(wrapped.chat(messages)) == "ok"

    assert provider.calls == 2
    assert any(
        any(tool_call.get("id") == "call_reactive" for tool_call in message.get("tool_calls") or [])
        for message in provider.messages
    )
    assert any(message.get("tool_call_id") == "call_reactive" for message in provider.messages)


def test_prompt_too_long_retry_fallback_trims_oldest_and_preserves_boundary_and_tool_pair():
    class FailingTwiceProvider(LLMProvider):
        name = "failing_twice"

        def __init__(self):
            self.calls = 0
            self.messages = []

        async def chat(self, messages, model=None, temperature=None, tools=None):  # noqa: ANN001, ARG002
            self.calls += 1
            self.messages = messages
            if self.calls <= 2:
                raise PromptTooLongError("prompt too long")
            return "ok"

        async def structured_chat(self, messages, output_schema):  # noqa: ANN001, ARG002
            return {"ok": True}

    provider = FailingTwiceProvider()
    wrapped = ContextAwareProvider(
        provider,
        _settings(
            model_context_window=120,
            max_tokens=20,
            context_manual_compact_buffer_tokens=10,
            model_auto_compact_token_limit=100000,
            context_history_snip_enabled=False,
            context_micro_compact_enabled=False,
            context_recent_message_limit=20,
        ),
    )
    messages = [
        {"id": "sys", "role": "system", "content": "policy"},
        {"id": "old", "role": "user", "content": "old " * 120},
        {
            "id": "boundary",
            "role": "system",
            "content": "latest compact summary",
            "metadata": {"context_boundary": "manual_compact", "compact_boundary": True},
        },
        {"id": "middle", "role": "user", "content": "middle " * 120},
        {
            "id": "call_owner",
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_keep",
                    "type": "function",
                    "function": {"name": "system.get_info", "arguments": "{}"},
                }
            ],
        },
        {"id": "call_result", "role": "tool", "tool_call_id": "call_keep", "content": "tool output"},
        {"id": "latest", "role": "user", "content": "latest request"},
    ]

    assert asyncio.run(wrapped.chat(messages)) == "ok"

    ids = [message.get("id") for message in provider.messages]
    assert provider.calls == 3
    assert "sys" in ids
    assert "boundary" in ids
    assert "old" not in ids
    assert "call_owner" in ids
    assert "call_result" in ids
    assert "latest" in ids
