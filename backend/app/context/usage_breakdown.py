from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.context.tokens import count_message_tokens, rough_token_count


def message_breakdown(messages: list[dict[str, Any]]) -> dict[str, Any]:
    tool_use_id_to_name: dict[str, str] = {}
    tool_call_tokens = 0
    tool_calls_by_type: dict[str, dict[str, int]] = {}
    tool_result_tokens = 0
    tool_results_by_type: dict[str, int] = {}
    attachment_tokens = 0
    attachments_by_type: dict[str, int] = {}
    assistant_tokens = 0
    user_tokens = 0

    for message in messages:
        role = str(message.get("role") or "")
        if role == "assistant":
            content_tokens = rough_token_count(message.get("content"))
            calls = list(message.get("tool_calls") or [])
            if calls:
                call_tokens = rough_token_count(calls)
                tool_call_tokens += call_tokens
                for call in calls:
                    name = _tool_call_name(call)
                    call_id = str(call.get("id") or "").strip() if isinstance(call, dict) else ""
                    if call_id:
                        tool_use_id_to_name[call_id] = name
                    item = tool_calls_by_type.setdefault(name, {"callTokens": 0, "resultTokens": 0})
                    item["callTokens"] += rough_token_count(call)
            else:
                assistant_tokens += content_tokens + 4
            continue
        if role == "tool":
            tokens = count_message_tokens(message)
            tool_result_tokens += tokens
            name = tool_use_id_to_name.get(str(message.get("tool_call_id") or "").strip(), "unknown")
            tool_results_by_type[name] = tool_results_by_type.get(name, 0) + tokens
            item = tool_calls_by_type.setdefault(name, {"callTokens": 0, "resultTokens": 0})
            item["resultTokens"] += tokens
            continue
        attachments = _attachment_breakdown(message.get("content"))
        if attachments:
            attachment_tokens += sum(tokens for _name, tokens in attachments)
            for name, tokens in attachments:
                attachments_by_type[name] = attachments_by_type.get(name, 0) + tokens
        if role == "user":
            user_tokens += count_message_tokens(message)
        elif role not in {"system", "developer"}:
            assistant_tokens += count_message_tokens(message)

    return {
        "toolCallTokens": tool_call_tokens,
        "toolResultTokens": tool_result_tokens,
        "attachmentTokens": attachment_tokens,
        "assistantMessageTokens": assistant_tokens,
        "userMessageTokens": user_tokens,
        "toolCallsByType": [
            {"name": name, "callTokens": values["callTokens"], "resultTokens": values["resultTokens"]}
            for name, values in sorted(
                tool_calls_by_type.items(),
                key=lambda item: item[1]["callTokens"] + item[1]["resultTokens"],
                reverse=True,
            )
        ],
        "attachmentsByType": [
            {"name": name, "tokens": tokens}
            for name, tokens in sorted(attachments_by_type.items(), key=lambda item: item[1], reverse=True)
        ],
        "toolResultsByType": [
            {"name": name, "tokens": tokens}
            for name, tokens in sorted(tool_results_by_type.items(), key=lambda item: item[1], reverse=True)
        ],
    }


def split_tool_definitions(tools: Iterable[Any]) -> tuple[list[Any], list[Any]]:
    local: list[Any] = []
    mcp: list[Any] = []
    for tool in tools:
        name = tool_name(tool)
        if name.startswith("mcp."):
            mcp.append(tool)
        else:
            local.append(tool)
    return local, mcp


def count_tools_tokens(tools: Iterable[Any]) -> int:
    return rough_token_count([tool_payload(tool) for tool in tools])


def tool_payload(tool: Any) -> dict[str, Any]:
    if hasattr(tool, "name"):
        return {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
            "input_schema": getattr(tool, "input_schema", {}),
            "output_schema": getattr(tool, "output_schema", {}),
            "risk_level": str(getattr(tool, "risk_level", "")),
            "agent_owner": getattr(tool, "agent_owner", ""),
            "search_hint": getattr(tool, "search_hint", ""),
        }
    if isinstance(tool, dict):
        return {
            "name": tool.get("name") or "",
            "description": tool.get("description") or "",
            "input_schema": tool.get("input_schema") or tool.get("inputSchema") or {},
            "output_schema": tool.get("output_schema") or tool.get("outputSchema") or {},
            "server": tool.get("server") or "",
        }
    return {"name": str(tool)}


def tool_name(tool: Any) -> str:
    if hasattr(tool, "name"):
        return str(tool.name or "")
    if isinstance(tool, dict):
        server = str(tool.get("server") or "")
        name = str(tool.get("name") or "")
        return f"mcp.{server}.{name}" if server and not name.startswith("mcp.") else name
    return str(tool)


def tool_server_name(tool: Any) -> str:
    if isinstance(tool, dict):
        server = str(tool.get("server") or "")
        if server:
            return server
        name = str(tool.get("name") or "")
        parts = name.split(".")
        if len(parts) >= 3 and parts[0] == "mcp":
            return parts[1]
    name = tool_name(tool)
    parts = name.split(".")
    if len(parts) >= 3 and parts[0] == "mcp":
        return parts[1]
    return ""


def count_tool_attr(tools: Iterable[Any], attr: str, value: Any) -> int:
    return sum(1 for tool in tools if getattr(tool, attr, None) == value)


def tools_breakdown(tools: Iterable[Any]) -> dict[str, Any]:
    by_tool: list[dict[str, Any]] = []
    by_server: dict[str, int] = {}
    for tool in tools:
        payload = tool_payload(tool)
        tokens = rough_token_count(payload)
        name = str(payload.get("name") or "")
        server = str(payload.get("server") or "")
        if server:
            by_server[server] = by_server.get(server, 0) + tokens
        by_tool.append({"name": name, "tokens": tokens, "server": server})
    return {
        "by_tool": by_tool,
        "by_server": by_server,
        "loaded_tokens": sum(item["tokens"] for item in by_tool),
        "deferred_tokens": 0,
    }


def _tool_call_name(tool_call: Any) -> str:
    if not isinstance(tool_call, dict):
        return "unknown"
    function = tool_call.get("function") or {}
    if isinstance(function, dict) and function.get("name"):
        return str(function.get("name"))
    return str(tool_call.get("name") or tool_call.get("type") or "unknown")


def _attachment_breakdown(content: Any) -> list[tuple[str, int]]:
    if not isinstance(content, list):
        return []
    result: list[tuple[str, int]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        block_type = str(item.get("type") or "")
        if block_type in {"image", "image_url", "document", "input_audio"}:
            result.append((block_type, rough_token_count(item)))
    return result
