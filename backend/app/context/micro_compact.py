from __future__ import annotations

import copy
import json
from typing import Any

from app.config import AppSettings
from app.context.compact_boundaries import compact_metadata as _compact_metadata
from app.context.message_text import content_text as _content_text
from app.context.message_text import single_line as _single_line
from app.context.tokens import ATTACHMENT_BLOCK_TYPES, count_message_tokens


def micro_compact_messages(messages: list[dict[str, Any]], settings: AppSettings) -> tuple[list[dict[str, Any]], bool]:
    result, changed, _metadata = micro_compact_messages_with_metadata(messages, settings)
    return result, changed


def micro_compact_messages_with_metadata(
    messages: list[dict[str, Any]],
    settings: AppSettings,
) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
    max_chars = max(0, int(settings.context_micro_compact_tool_result_chars))
    age = max(0, int(settings.context_micro_compact_age))
    metadata = empty_micro_compact_metadata()
    if max_chars <= 0 or not messages:
        return messages, False, metadata

    compactable_limit = max(0, len(messages) - age)
    changed = False
    result = list(messages)
    tool_context = tool_context_by_id(messages)
    for index, message in enumerate(messages):
        if index >= compactable_limit:
            continue
        role = str(message.get("role") or "")
        if role in {"system", "developer"}:
            continue

        compacted, message_changed, saved = _micro_compact_message(
            message,
            role=role,
            max_chars=max_chars,
            tool_context=tool_context,
        )
        if not message_changed:
            continue

        result[index] = compacted
        _merge_message_micro_metadata(metadata, compacted, saved)
        changed = True
    return result, changed, metadata


def empty_micro_compact_metadata() -> dict[str, Any]:
    return {
        "tokens_saved": 0,
        "compacted_tool_ids": [],
        "cleared_attachment_ids": [],
        "collapsed_tool_results": [],
        "cleared_attachments": [],
    }


def projection_compact_metadata(boundary: dict[str, Any], micro_metadata: dict[str, Any]) -> dict[str, Any]:
    compact_metadata = _compact_metadata(boundary)
    if not has_micro_compact_metadata(micro_metadata):
        return compact_metadata

    merged = dict(compact_metadata)
    merged["tokens_saved"] = max(0, int(merged.get("tokens_saved") or 0)) + max(
        0,
        int(micro_metadata.get("tokens_saved") or 0),
    )
    for key in ("compacted_tool_ids", "cleared_attachment_ids"):
        values = list(merged.get(key) or [])
        for value in micro_metadata.get(key) or []:
            append_unique(values, str(value))
        merged[key] = values
    merged["micro_compact"] = {
        "tokens_saved": max(0, int(micro_metadata.get("tokens_saved") or 0)),
        "compacted_tool_ids": list(micro_metadata.get("compacted_tool_ids") or []),
        "cleared_attachment_ids": list(micro_metadata.get("cleared_attachment_ids") or []),
        "collapsed_tool_results": list(micro_metadata.get("collapsed_tool_results") or []),
        "cleared_attachments": list(micro_metadata.get("cleared_attachments") or []),
    }
    return merged


def has_micro_compact_metadata(metadata: dict[str, Any]) -> bool:
    return bool(
        int(metadata.get("tokens_saved") or 0) > 0
        or metadata.get("compacted_tool_ids")
        or metadata.get("cleared_attachment_ids")
    )


def tool_context_by_id(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    for message in messages:
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            tool_call_id = str(tool_call.get("id") or "").strip()
            if not tool_call_id:
                continue
            function = tool_call.get("function") or {}
            function = function if isinstance(function, dict) else {}
            context[tool_call_id] = {
                "tool_name": tool_call_name(tool_call),
                "arguments": parse_tool_arguments(function.get("arguments")),
            }
    return context


def tool_call_name(tool_call: dict[str, Any]) -> str:
    function = tool_call.get("function") or {}
    if isinstance(function, dict) and function.get("name"):
        return str(function.get("name") or "")
    return str(tool_call.get("name") or tool_call.get("type") or "unknown")


def parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return dict(arguments)
    if not isinstance(arguments, str) or not arguments.strip():
        return {}
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return {"raw": arguments}
    return dict(parsed) if isinstance(parsed, dict) else {"value": parsed}


def collapse_attachment_blocks(content: Any, *, message: dict[str, Any]) -> tuple[Any, list[str]]:
    if isinstance(content, dict):
        block_type = str(content.get("type") or "")
        if block_type not in ATTACHMENT_BLOCK_TYPES:
            return content, []
        attachment_id = _attachment_id(content, message, 0)
        return _attachment_placeholder(block_type, attachment_id), [attachment_id]

    if not isinstance(content, list):
        return content, []

    collapsed: list[Any] = []
    cleared_ids: list[str] = []
    changed = False
    for index, item in enumerate(content):
        if not isinstance(item, dict):
            collapsed.append(item)
            continue
        block_type = str(item.get("type") or "")
        if block_type not in ATTACHMENT_BLOCK_TYPES:
            collapsed.append(copy.deepcopy(item))
            continue
        attachment_id = _attachment_id(item, message, index)
        cleared_ids.append(attachment_id)
        collapsed.append({"type": "text", "text": _attachment_placeholder(block_type, attachment_id)})
        changed = True
    return (collapsed if changed else content), cleared_ids


def tool_result_collapse_summary(
    message: dict[str, Any],
    content_text: str,
    *,
    max_chars: int,
    tool_context: dict[str, Any],
) -> dict[str, Any]:
    metadata = message.get("metadata") or {}
    tool_name = str(tool_context.get("tool_name") or metadata.get("tool_name") or "unknown")
    arguments = tool_context.get("arguments") if isinstance(tool_context.get("arguments"), dict) else {}
    kind = _tool_result_kind(tool_name)
    detail = _tool_result_detail(kind, arguments)

    lines = [
        "[Tool result collapsed for projection]",
        f"tool: {tool_name}",
        f"kind: {kind}",
        f"original_chars: {len(content_text)}",
    ]
    if detail:
        lines.append(detail)
    header = "\n".join(lines)
    if len(header) > max_chars:
        header = header[: max(1, max_chars - 24)].rstrip() + "\n[summary truncated]"
    return {
        "content": header,
        "kind": kind,
        "tool_name": tool_name,
        "original_chars": len(content_text),
    }


def append_unique(values: list[str], value: str) -> None:
    normalized = str(value or "").strip()
    if normalized and normalized not in values:
        values.append(normalized)


def _micro_compact_message(
    message: dict[str, Any],
    *,
    role: str,
    max_chars: int,
    tool_context: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], bool, int]:
    before_tokens = count_message_tokens(message)
    cleared_attachment_ids: list[str] = []
    collapsed_attachment_content, cleared_attachment_ids = collapse_attachment_blocks(
        message.get("content"),
        message=message,
    )

    compacted_tool_id = ""
    collapse_summary: dict[str, Any] = {}
    content = message.get("content") or ""
    if role == "tool":
        tool_call_id = str(message.get("tool_call_id") or "").strip()
        context = tool_context.get(tool_call_id, {})
        content_text = _content_text(content)
        if len(content_text) > max_chars:
            compacted_tool_id = tool_call_id or str(message.get("id") or "").strip()
            collapse_summary = tool_result_collapse_summary(
                message,
                content_text,
                max_chars=max_chars,
                tool_context=context,
            )
            collapsed_attachment_content = collapse_summary["content"]

    if not cleared_attachment_ids and not compacted_tool_id:
        return message, False, 0

    updated = dict(message)
    updated["content"] = collapsed_attachment_content
    after_tokens = count_message_tokens(updated)
    tokens_saved = max(0, before_tokens - after_tokens)
    updated["metadata"] = _message_micro_metadata(
        updated,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        tokens_saved=tokens_saved,
        compacted_tool_id=compacted_tool_id,
        collapse_summary=collapse_summary,
        original_tool_content=content,
        cleared_attachment_ids=cleared_attachment_ids,
    )
    return updated, True, tokens_saved


def _message_micro_metadata(
    message: dict[str, Any],
    *,
    before_tokens: int,
    after_tokens: int,
    tokens_saved: int,
    compacted_tool_id: str,
    collapse_summary: dict[str, Any],
    original_tool_content: Any,
    cleared_attachment_ids: list[str],
) -> dict[str, Any]:
    message_metadata = dict(message.get("metadata") or {})
    message_metadata["micro_compacted"] = True
    message_metadata["original_tokens"] = before_tokens
    message_metadata["projected_tokens"] = after_tokens
    if tokens_saved:
        message_metadata["tokens_saved"] = tokens_saved
    if compacted_tool_id:
        message_metadata["original_chars"] = collapse_summary.get(
            "original_chars",
            len(_content_text(original_tool_content)),
        )
        message_metadata["collapse_kind"] = collapse_summary.get("kind", "tool_result")
        message_metadata["tool_name"] = collapse_summary.get("tool_name", "")
        message_metadata["compacted_tool_id"] = compacted_tool_id
    if cleared_attachment_ids:
        message_metadata["cleared_attachment_ids"] = cleared_attachment_ids
    return message_metadata


def _merge_message_micro_metadata(metadata: dict[str, Any], message: dict[str, Any], tokens_saved: int) -> None:
    message_metadata = message.get("metadata") or {}
    compacted_tool_id = str(message_metadata.get("compacted_tool_id") or "").strip()
    if compacted_tool_id:
        append_unique(metadata["compacted_tool_ids"], compacted_tool_id)
        metadata["collapsed_tool_results"].append(
            {
                "message_id": str(message.get("id") or "").strip(),
                "tool_call_id": compacted_tool_id,
                "tool_name": message_metadata.get("tool_name", ""),
                "kind": message_metadata.get("collapse_kind", "tool_result"),
                "original_chars": message_metadata.get("original_chars", 0),
                "projected_chars": len(str(message.get("content") or "")),
                "tokens_saved": tokens_saved,
            }
        )

    cleared_attachment_ids = [str(value) for value in message_metadata.get("cleared_attachment_ids") or []]
    if cleared_attachment_ids:
        for attachment_id in cleared_attachment_ids:
            append_unique(metadata["cleared_attachment_ids"], attachment_id)
        metadata["cleared_attachments"].extend(
            {
                "message_id": str(message.get("id") or "").strip(),
                "attachment_id": attachment_id,
            }
            for attachment_id in cleared_attachment_ids
        )
    metadata["tokens_saved"] += tokens_saved


def _attachment_id(item: dict[str, Any], message: dict[str, Any], index: int) -> str:
    for key in ("id", "attachment_id", "attachmentId", "file_id", "fileId", "name", "path", "source"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    message_id = str(message.get("id") or "").strip() or "message"
    return f"{message_id}:attachment:{index}"


def _attachment_placeholder(block_type: str, attachment_id: str) -> str:
    return f"[{block_type} attachment cleared from projection: {attachment_id}]"


def _tool_result_kind(tool_name: str) -> str:
    normalized = tool_name.casefold()
    if any(marker in normalized for marker in ("shell", "bash", "command", "terminal")):
        return "bash"
    if any(marker in normalized for marker in ("search", "grep", "glob", "query", "fetch_result", "summarize_results")):
        return "search"
    if any(marker in normalized for marker in ("read", "list", "metadata", "hash", "diff", "preview", "get")):
        return "read"
    return "tool_result"


def _tool_result_detail(kind: str, arguments: dict[str, Any]) -> str:
    if kind == "bash":
        command = str(arguments.get("command") or arguments.get("raw") or "").strip()
        return f"command: {_single_line(command)[:240]}" if command else ""
    if kind == "search":
        query = str(
            arguments.get("query") or arguments.get("pattern") or arguments.get("q") or arguments.get("raw") or ""
        ).strip()
        return f"query: {_single_line(query)[:240]}" if query else ""
    if kind == "read":
        path = str(
            arguments.get("path")
            or arguments.get("paths")
            or arguments.get("source")
            or arguments.get("source_path")
            or ""
        ).strip()
        return f"target: {_single_line(path)[:240]}" if path else ""
    return ""
