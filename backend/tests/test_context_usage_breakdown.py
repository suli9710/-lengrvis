from __future__ import annotations

from typing import Any

from app.config import AppSettings
from app.context.tokens import count_message_tokens, rough_token_count
from app.context_usage import (
    MCP_TOOLS_CATEGORY,
    TOOLS_REGISTRY_CATEGORY,
    analyze_context_usage,
    context_usage_to_dict,
)


def _settings(**overrides: Any) -> AppSettings:
    settings = AppSettings(
        model_context_window=10000,
        model_auto_compact_token_limit=8000,
        max_tokens=100,
        context_manual_compact_buffer_tokens=50,
        context_warning_buffer_tokens=20,
        context_error_buffer_tokens=10,
        context_auto_compact_enabled=False,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _usage_payload(
    *,
    messages: list[dict[str, Any]] | None = None,
    tool_definitions: list[Any] | None = None,
    mcp_tools: list[Any] | None = None,
) -> dict[str, Any]:
    return context_usage_to_dict(
        analyze_context_usage(
            messages=messages or [],
            tool_definitions=tool_definitions or [],
            mcp_tools=mcp_tools or [],
            session_context={},
            settings=_settings(),
            include_registered_tools=False,
        )
    )


def _category(payload: dict[str, Any], category_id: str) -> dict[str, Any]:
    return next(category for category in payload["categories"] if category["id"] == category_id)


def test_message_breakdown_tracks_tool_calls_and_results_by_tool_name():
    tool_calls = [
        {
            "id": "call_read",
            "type": "function",
            "function": {"name": "file.read", "arguments": '{"path":"README.md"}'},
        },
        {
            "id": "call_shell",
            "type": "function",
            "function": {"name": "developer.shell", "arguments": '{"cmd":"pytest"}'},
        },
    ]
    tool_results = [
        {"role": "tool", "tool_call_id": "call_read", "content": "readme contents"},
        {
            "role": "tool",
            "tool_call_id": "call_shell",
            "content": {"type": "tool_result", "content": "pytest passed"},
        },
    ]
    messages = [
        {"role": "user", "content": "Run the checks."},
        {"role": "assistant", "content": "", "tool_calls": tool_calls},
        *tool_results,
    ]

    breakdown = _usage_payload(messages=messages)["breakdown"]["messages"]

    assert breakdown["toolCallTokens"] == rough_token_count(tool_calls)
    assert breakdown["toolResultTokens"] == sum(count_message_tokens(message) for message in tool_results)
    calls_by_type = {item["name"]: item for item in breakdown["toolCallsByType"]}
    assert calls_by_type["file.read"]["callTokens"] == rough_token_count(tool_calls[0])
    assert calls_by_type["file.read"]["resultTokens"] == count_message_tokens(tool_results[0])
    assert calls_by_type["developer.shell"]["callTokens"] == rough_token_count(tool_calls[1])
    assert calls_by_type["developer.shell"]["resultTokens"] == count_message_tokens(tool_results[1])
    assert {item["name"]: item["tokens"] for item in breakdown["toolResultsByType"]} == {
        "file.read": count_message_tokens(tool_results[0]),
        "developer.shell": count_message_tokens(tool_results[1]),
    }


def test_message_breakdown_counts_attachment_tokens_by_block_type():
    content = [
        {"type": "text", "text": "Please inspect these attachments."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "notes"}},
        {"type": "input_audio", "input_audio": {"data": "abc", "format": "mp3"}},
    ]
    message = {"role": "user", "content": content}

    breakdown = _usage_payload(messages=[message])["breakdown"]["messages"]

    expected_by_type = {
        "image_url": rough_token_count(content[1]),
        "document": rough_token_count(content[2]),
        "input_audio": rough_token_count(content[3]),
    }
    assert breakdown["attachmentTokens"] == sum(expected_by_type.values())
    assert {item["name"]: item["tokens"] for item in breakdown["attachmentsByType"]} == expected_by_type
    assert breakdown["userMessageTokens"] == count_message_tokens(message)


def test_tool_breakdown_keeps_local_and_mcp_tools_separate():
    local_tool = {
        "name": "file.read",
        "description": "Read a workspace file",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }
    registry_mcp_tool = {
        "name": "search",
        "server": "docs",
        "description": "Search documentation",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }
    explicit_mcp_tool = {
        "name": "lookup",
        "server": "memory",
        "description": "Lookup memory",
        "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}},
    }

    payload = _usage_payload(
        tool_definitions=[local_tool, registry_mcp_tool],
        mcp_tools=[explicit_mcp_tool],
    )

    tool_totals = payload["breakdown"]["tools"]
    local_category = _category(payload, TOOLS_REGISTRY_CATEGORY)
    mcp_category = _category(payload, MCP_TOOLS_CATEGORY)
    assert tool_totals["registered_count"] == 1
    assert tool_totals["mcp_count"] == 2
    assert local_category["item_count"] == 1
    assert mcp_category["item_count"] == 2
    assert payload["lineage"]["local_tool_count"] == 1
    assert payload["lineage"]["mcp_tool_count"] == 2

    local_breakdown = local_category["details"]["breakdown"]
    mcp_breakdown = mcp_category["details"]["breakdown"]
    assert local_breakdown["by_tool"] == [
        {"name": "file.read", "tokens": tool_totals["registered_tokens"], "server": ""}
    ]
    assert local_breakdown["by_server"] == {}
    assert set(mcp_breakdown["by_server"]) == {"docs", "memory"}
    assert {(item["server"], item["name"]) for item in mcp_breakdown["by_tool"]} == {
        ("docs", "search"),
        ("memory", "lookup"),
    }
    assert {(item["serverName"], item["name"]) for item in payload["claude_view"]["mcpTools"]} == {
        ("docs", "search"),
        ("memory", "lookup"),
    }
