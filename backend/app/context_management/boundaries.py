from __future__ import annotations

import copy
from typing import Any

from .constants import COMPACT_BOUNDARY_TYPES


def _tool_call_ids(message: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = str(tool_call.get("id") or "").strip()
        if tool_call_id:
            ids.add(tool_call_id)
    return ids


def compact_boundary_view(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the LLM-visible view after the newest compact boundary.

    Manual compaction persists a boundary/summary message plus a recent tail.
    If callers later pass a longer transcript that still contains older items,
    this keeps stable system/developer instructions and starts history at the
    latest compact boundary.
    """

    boundary_index = _latest_compact_boundary_index(messages)
    if boundary_index is None:
        return messages
    boundary = copy.deepcopy(messages[boundary_index])
    retained_tail_ids = _retained_tail_message_ids(boundary)
    preserved_segment = _preserved_segment_with_tool_call_owners(
        messages[:boundary_index],
        _preserved_segment_messages(boundary),
    )
    retained_tail_ids = _expand_tool_pair_message_ids(
        messages[:boundary_index],
        retained_tail_ids,
    )
    protected_head = [
        copy.deepcopy(message)
        for message in messages[:boundary_index]
        if message.get("role") in {"system", "developer"} and not _is_compact_boundary(message)
    ]
    tail_from_metadata: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    if retained_tail_ids:
        for message in messages[:boundary_index]:
            message_id = str(message.get("id") or "").strip()
            if message_id in retained_tail_ids and message_id not in seen_ids and not _is_compact_boundary(message):
                tail_from_metadata.append(copy.deepcopy(message))
                seen_ids.add(message_id)
    for message in preserved_segment:
        if _is_compact_boundary(message):
            continue
        message_id = str(message.get("id") or "").strip()
        if message_id and message_id in seen_ids:
            continue
        tail_from_metadata.append(copy.deepcopy(message))
        if message_id:
            seen_ids.add(message_id)
    tail_after_boundary = copy.deepcopy(messages[boundary_index + 1 :])
    if tail_after_boundary:
        after_ids = {str(message.get("id") or "").strip() for message in tail_after_boundary}
        tail_from_metadata = [message for message in tail_from_metadata if str(message.get("id") or "").strip() not in after_ids]
    return [*protected_head, boundary, *tail_from_metadata, *tail_after_boundary]


def _protected_head_end_unused() -> None:
    return None


def _latest_compact_boundary_index(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if _is_compact_boundary(messages[index]):
            return index
    return None


def _latest_compact_boundary(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    index = _latest_compact_boundary_index(messages)
    if index is None:
        return None
    return messages[index]


def _is_compact_boundary(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    boundary = str(metadata.get("context_boundary") or "")
    compact_metadata = _compact_metadata(message)
    compact_boundary = str(
        compact_metadata.get("context_boundary")
        or compact_metadata.get("boundary_type")
        or compact_metadata.get("type")
        or ""
    )
    return (
        boundary in COMPACT_BOUNDARY_TYPES
        or compact_boundary in COMPACT_BOUNDARY_TYPES
        or bool(metadata.get("compact_boundary"))
        or bool(compact_metadata.get("compact_boundary"))
    )


def _retained_tail_message_ids(boundary: dict[str, Any]) -> set[str]:
    metadata = boundary.get("metadata") or {}
    if not isinstance(metadata, dict):
        return set()
    compact_metadata = metadata.get("compact_metadata") or metadata.get("compactMetadata") or {}
    raw_values = [metadata.get("retained_tail_message_ids")]
    if isinstance(compact_metadata, dict):
        raw_values.extend(
            [
                compact_metadata.get("retained_tail_message_ids"),
                compact_metadata.get("messages_to_keep_ids"),
                compact_metadata.get("messagesToKeep"),
                compact_metadata.get("preserved_message_ids"),
                compact_metadata.get("preserved_segment_message_ids"),
            ]
        )
        preserved = compact_metadata.get("preserved_segment") or compact_metadata.get("preservedSegment") or {}
        if isinstance(preserved, dict):
            raw_values.append(preserved.get("message_ids") or preserved.get("messageIds"))
    message_ids: set[str] = set()
    for raw_ids in raw_values:
        if isinstance(raw_ids, list):
            message_ids.update(str(item).strip() for item in raw_ids if str(item).strip())
    return message_ids


def _preserved_segment_with_tool_call_owners(
    prior_messages: list[dict[str, Any]],
    preserved_segment: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not preserved_segment:
        return []
    tool_call_owners: dict[str, dict[str, Any]] = {}
    for message in prior_messages:
        for tool_call_id in _tool_call_ids(message):
            tool_call_owners[tool_call_id] = message

    result: list[dict[str, Any]] = []
    emitted_ids = {str(message.get("id") or "").strip() for message in preserved_segment if str(message.get("id") or "").strip()}
    for message in preserved_segment:
        tool_call_id = str(message.get("tool_call_id") or "").strip() if str(message.get("role") or "") == "tool" else ""
        owner = tool_call_owners.get(tool_call_id)
        owner_id = str((owner or {}).get("id") or "").strip()
        if owner and owner_id not in emitted_ids:
            result.append(copy.deepcopy(owner))
            emitted_ids.add(owner_id)
        result.append(copy.deepcopy(message))
    return result


def _compact_metadata(boundary: dict[str, Any]) -> dict[str, Any]:
    metadata = boundary.get("metadata") or {}
    if not isinstance(metadata, dict):
        return {}
    compact_metadata = metadata.get("compact_metadata") or metadata.get("compactMetadata") or {}
    if isinstance(compact_metadata, dict):
        return dict(compact_metadata)
    return {}


def redact_compact_metadata(compact_metadata: dict[str, Any]) -> dict[str, Any]:
    """Return compact metadata safe for API responses and telemetry."""

    redacted = copy.deepcopy(compact_metadata)
    preserved = redacted.get("preserved_segment") or redacted.get("preservedSegment")
    if isinstance(preserved, dict):
        raw_messages = preserved.pop("messages", [])
        if isinstance(raw_messages, list):
            preserved["message_count"] = len([message for message in raw_messages if isinstance(message, dict)])
        redacted["preserved_segment"] = preserved
        redacted.pop("preservedSegment", None)
    return redacted


def _preserved_segment_messages(boundary: dict[str, Any]) -> list[dict[str, Any]]:
    compact_metadata = _compact_metadata(boundary)
    preserved = compact_metadata.get("preserved_segment") or compact_metadata.get("preservedSegment") or []
    raw_messages = preserved.get("messages") if isinstance(preserved, dict) else preserved
    if not isinstance(raw_messages, list):
        return []
    return [copy.deepcopy(message) for message in raw_messages if isinstance(message, dict)]


def _expand_tool_pair_message_ids(messages: list[dict[str, Any]], ids: set[str]) -> set[str]:
    if not ids:
        return set()
    expanded = set(ids)
    id_by_tool_call: dict[str, str] = {}
    tool_call_owner_ids: dict[str, str] = {}
    for message in messages:
        message_id = str(message.get("id") or "").strip()
        for tool_call_id in _tool_call_ids(message):
            tool_call_owner_ids[tool_call_id] = message_id
        if str(message.get("role") or "") == "tool":
            tool_call_id = str(message.get("tool_call_id") or "").strip()
            if tool_call_id and message_id:
                id_by_tool_call[tool_call_id] = message_id
    for tool_call_id, owner_id in tool_call_owner_ids.items():
        result_id = id_by_tool_call.get(tool_call_id, "")
        if owner_id in expanded and result_id:
            expanded.add(result_id)
        if result_id in expanded and owner_id:
            expanded.add(owner_id)
    return expanded
