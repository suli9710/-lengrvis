from __future__ import annotations

from typing import Any

from app.config import AppSettings
from app.context.message_text import content_text as _content_text
from app.context.message_text import single_line as _single_line
from app.context.micro_compact import (
    parse_tool_arguments as _parse_tool_arguments,
)
from app.context.micro_compact import (
    tool_call_name as _tool_call_name,
)
from app.context.micro_compact import (
    tool_context_by_id as _tool_context_by_id,
)
from app.context.micro_compact import (
    tool_result_collapse_summary as _tool_result_collapse_summary,
)


def summarize_messages(messages: list[dict[str, Any]], settings: AppSettings) -> str:
    if not messages:
        return ""
    limit = max(500, int(settings.context_session_summary_limit))
    entries = _semantic_summary_entries(messages)
    if not entries:
        return ""
    return _fit_summary_entries(entries, limit)


def _semantic_summary_entries(messages: list[dict[str, Any]]) -> list[tuple[int, int, str]]:
    role_counts: dict[str, int] = {}
    latest_user_index = -1
    latest_user_text = ""
    for index, message in enumerate(messages):
        role = str(message.get("role") or "assistant")
        role_counts[role] = role_counts.get(role, 0) + 1
        if role == "user":
            text = _single_line(_content_text(message.get("content")))
            if text:
                latest_user_index = index
                latest_user_text = text

    entries: list[tuple[int, int, str]] = []
    counts = ", ".join(f"{role}={count}" for role, count in sorted(role_counts.items()))
    entries.append((0, -2, f"- Covered {len(messages)} earlier message(s) before compaction ({counts})."))
    if latest_user_text:
        entries.append((0, -1, f"- Latest user intent before compaction: {_clip_summary_text(latest_user_text, 420)}"))

    tool_context_by_id = _tool_context_by_id(messages)
    for index, message in enumerate(messages):
        line = _semantic_summary_line(message, tool_context_by_id=tool_context_by_id)
        if not line:
            continue
        role = str(message.get("role") or "assistant")
        if role == "user" and index == latest_user_index:
            continue
        priority = 1 if _is_high_value_summary_message(message, line) else 2
        entries.append((priority, index, line))
    return sorted(entries, key=lambda item: (item[0], item[1]))


def _semantic_summary_line(message: dict[str, Any], *, tool_context_by_id: dict[str, dict[str, Any]]) -> str:
    role = str(message.get("role") or "assistant")
    name = str(message.get("name") or message.get("metadata", {}).get("from_agent") or "").strip()
    label = f"{role}:{name}" if name else role
    tool_calls = [tool_call for tool_call in message.get("tool_calls") or [] if isinstance(tool_call, dict)]
    text = _single_line(_content_text(message.get("content")))

    if tool_calls:
        tool_summary = ", ".join(_tool_call_brief(tool_call) for tool_call in tool_calls[:6])
        suffix = f"; note: {_clip_summary_text(text, 220)}" if text else ""
        return f"- {label} requested tool(s): {tool_summary}{suffix}"

    if role == "tool":
        tool_call_id = str(message.get("tool_call_id") or "").strip()
        tool_context = tool_context_by_id.get(tool_call_id, {})
        summary = _tool_result_collapse_summary(
            message,
            text,
            max_chars=520,
            tool_context=tool_context,
        )
        return f"- tool result: {_clip_summary_text(_single_line(summary['content']), 520)}"

    if not text:
        return ""
    return f"- {label}: {_clip_summary_text(text, 420)}"


def _tool_call_brief(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    function = function if isinstance(function, dict) else {}
    arguments = _parse_tool_arguments(function.get("arguments"))
    arg_keys = ", ".join(sorted(arguments.keys())[:6])
    return f"{_tool_call_name(tool_call)}({arg_keys})" if arg_keys else f"{_tool_call_name(tool_call)}()"


def _is_high_value_summary_message(message: dict[str, Any], line: str) -> bool:
    role = str(message.get("role") or "")
    if role == "tool" or message.get("tool_calls"):
        return True
    lowered = line.casefold()
    markers = (
        "approved",
        "blocked",
        "decision",
        "denied",
        "error",
        "failed",
        "next",
        "permission",
        "plan",
        "risk",
        "todo",
    )
    return any(marker in lowered for marker in markers)


def _fit_summary_entries(entries: list[tuple[int, int, str]], limit: int) -> str:
    header = "Earlier conversation summary:\n"
    body_budget = max(1, limit - len(header))
    selected: list[str] = []
    used = 0
    omitted = 0
    for _priority, _index, raw_line in entries:
        line = _clip_summary_text(raw_line, min(700, body_budget)).rstrip()
        line_size = len(line) + (1 if selected else 0)
        if used + line_size <= body_budget or not selected:
            selected.append(line)
            used += line_size
        else:
            omitted += 1
    if omitted:
        marker = f"- [omitted {omitted} lower-priority summary item(s) due to context budget]"
        marker_size = len(marker) + (1 if selected else 0)
        while selected and used + marker_size > body_budget:
            removed = selected.pop()
            used -= len(removed) + (1 if selected else 0)
            omitted += 1
            marker = f"- [omitted {omitted} lower-priority summary item(s) due to context budget]"
            marker_size = len(marker) + (1 if selected else 0)
        if used + marker_size <= body_budget:
            selected.append(marker)
    return header + "\n".join(selected)


def _clip_summary_text(text: str, max_chars: int) -> str:
    normalized = _single_line(text)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(1, max_chars - 15)].rstrip() + " [truncated]"


__all__ = ["summarize_messages"]
