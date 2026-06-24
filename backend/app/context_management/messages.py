from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from .boundaries import _is_compact_boundary, _tool_call_ids

if TYPE_CHECKING:
    from app.core.schemas import AgentMessage


def recent_complete_tail_start(messages: list[dict[str, Any]], keep_recent: int, *, min_start_index: int = 0) -> int:
    """Return a recent-tail start index that does not orphan tool results.

    OpenAI-compatible chat history is sensitive to assistant ``tool_calls`` and
    subsequent ``tool`` messages staying together. A plain ``messages[-N:]`` can
    start on a tool result and leave its assistant call behind, so compaction
    expands the tail backward until visible tool results have their call site.
    """

    if not messages:
        return 0
    floor = max(0, min(len(messages), int(min_start_index or 0)))
    start = max(floor, len(messages) - max(1, int(keep_recent or 1)))
    while start > floor:
        missing = _orphan_tool_result_ids(messages[start:])
        if not missing and str(messages[start].get("role") or "") != "tool":
            break
        previous = _nearest_prior_tool_call_index(messages, start, missing)
        if previous is None:
            if str(messages[start].get("role") or "") == "tool":
                start -= 1
                continue
            break
        if previous < floor:
            break
        start = previous
    return start


def select_recent_complete_tail(
    messages: list[dict[str, Any]],
    keep_recent: int,
    *,
    min_start_index: int = 0,
) -> list[dict[str, Any]]:
    return copy.deepcopy(messages[recent_complete_tail_start(messages, keep_recent, min_start_index=min_start_index) :])


def repair_tool_message_invariants(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a provider-safe view with atomic assistant/tool message blocks.

    Tool-call messages are only kept when their matching tool result is present
    in the immediately following tool-result block. Otherwise the structured
    envelope is removed and the readable content is preserved.
    """

    if not messages:
        return []

    repaired: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        item = copy.deepcopy(messages[index])
        role = str(item.get("role") or "")
        if role == "tool":
            repaired.append(_demote_orphan_tool_result(item))
            index += 1
            continue

        tool_calls = _valid_tool_calls(item)
        if not tool_calls:
            repaired.append(_drop_tool_calls(item) if item.get("tool_calls") else item)
            index += 1
            continue

        next_index = index + 1
        contiguous_tool_results: list[dict[str, Any]] = []
        while next_index < len(messages) and str(messages[next_index].get("role") or "") == "tool":
            contiguous_tool_results.append(copy.deepcopy(messages[next_index]))
            next_index += 1

        result_ids = {
            str(result.get("tool_call_id") or "").strip()
            for result in contiguous_tool_results
            if str(result.get("tool_call_id") or "").strip()
        }
        kept_tool_calls = [tool_call for tool_call in tool_calls if str(tool_call.get("id") or "").strip() in result_ids]
        if kept_tool_calls:
            kept_ids = {str(tool_call.get("id") or "").strip() for tool_call in kept_tool_calls}
            item["tool_calls"] = kept_tool_calls
            repaired.append(item)
            emitted_result_ids: set[str] = set()
            delayed_demotions: list[dict[str, Any]] = []
            for result in contiguous_tool_results:
                result_id = str(result.get("tool_call_id") or "").strip()
                if result_id in kept_ids and result_id not in emitted_result_ids:
                    repaired.append(result)
                    emitted_result_ids.add(result_id)
                else:
                    delayed_demotions.append(_demote_orphan_tool_result(result))
            repaired.extend(delayed_demotions)
        else:
            repaired.append(_drop_tool_calls(item))
            repaired.extend(_demote_orphan_tool_result(result) for result in contiguous_tool_results)
        index = next_index

    return repaired


def _protected_head_end(messages: list[dict[str, Any]]) -> int:
    index = 0
    while index < len(messages) and messages[index].get("role") in {"system", "developer"}:
        if _is_compact_boundary(messages[index]):
            break
        index += 1
    return index


def _valid_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        tool_call
        for tool_call in message.get("tool_calls") or []
        if isinstance(tool_call, dict) and str(tool_call.get("id") or "").strip()
    ]


def _demote_orphan_tool_result(message: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(message)
    item["role"] = "assistant"
    item.pop("tool_call_id", None)
    metadata = dict(item.get("metadata") or {})
    metadata["orphan_tool_result_compacted"] = True
    item["metadata"] = metadata
    if not str(item.get("content") or "").strip():
        item["content"] = "[Tool result omitted during context compaction because its tool call is not in view.]"
    return item


def _drop_tool_calls(message: dict[str, Any]) -> dict[str, Any]:
    item = copy.deepcopy(message)
    item.pop("tool_calls", None)
    metadata = dict(item.get("metadata") or {})
    metadata["tool_calls_compacted"] = True
    item["metadata"] = metadata
    if not str(item.get("content") or "").strip():
        item["content"] = "[Tool call omitted during context compaction because its result is not in view.]"
    return item


def _orphan_tool_result_ids(messages: list[dict[str, Any]]) -> set[str]:
    open_tool_call_ids: set[str] = set()
    missing: set[str] = set()
    for message in messages:
        role = str(message.get("role") or "")
        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            if tool_call_id and tool_call_id not in open_tool_call_ids:
                missing.add(tool_call_id)
            continue
        open_tool_call_ids.update(_tool_call_ids(message))
    return missing


def _nearest_prior_tool_call_index(
    messages: list[dict[str, Any]],
    start: int,
    wanted_ids: set[str],
) -> int | None:
    wanted = set(wanted_ids)
    if not wanted and 0 <= start < len(messages) and str(messages[start].get("role") or "") == "tool":
        wanted.add(str(messages[start].get("tool_call_id") or "").strip())
        wanted.discard("")
    for index in range(start - 1, -1, -1):
        ids = _tool_call_ids(messages[index])
        if wanted:
            if ids & wanted:
                return index
        elif ids:
            return index
    return None


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        role = str(item.get("role") or "user")
        item["role"] = role
        if item.get("content") is None:
            item["content"] = ""
        normalized.append(item)
    return normalized


def _system_context_message(content: str, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "system",
        "content": content,
        "metadata": metadata,
    }


def _message_to_llm_dict(message: "AgentMessage") -> dict[str, Any]:
    payload = message.to_openai_dict(include_legacy=False)
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("from_agent", message.from_agent)
    metadata.setdefault("message_type", message.message_type.value)
    payload["metadata"] = metadata
    return payload
