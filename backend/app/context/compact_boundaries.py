from __future__ import annotations

import copy
from typing import Any

COMPACT_BOUNDARY_TYPES = {"manual_compact", "auto_compact", "reactive_compact"}


def latest_compact_boundary_index(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if is_compact_boundary(messages[index]):
            return index
    return None


def latest_compact_boundary(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    index = latest_compact_boundary_index(messages)
    if index is None:
        return None
    return messages[index]


def is_compact_boundary(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata") or {}
    if not isinstance(metadata, dict):
        return False
    boundary = str(metadata.get("context_boundary") or "")
    metadata_from_boundary = compact_metadata(message)
    compact_boundary = str(
        metadata_from_boundary.get("context_boundary")
        or metadata_from_boundary.get("boundary_type")
        or metadata_from_boundary.get("type")
        or ""
    )
    return (
        boundary in COMPACT_BOUNDARY_TYPES
        or compact_boundary in COMPACT_BOUNDARY_TYPES
        or bool(metadata.get("compact_boundary"))
        or bool(metadata_from_boundary.get("compact_boundary"))
    )


def retained_tail_message_ids(boundary: dict[str, Any]) -> set[str]:
    metadata = boundary.get("metadata") or {}
    if not isinstance(metadata, dict):
        return set()
    metadata_from_boundary = metadata.get("compact_metadata") or metadata.get("compactMetadata") or {}
    raw_values = [metadata.get("retained_tail_message_ids")]
    if isinstance(metadata_from_boundary, dict):
        raw_values.extend(
            [
                metadata_from_boundary.get("retained_tail_message_ids"),
                metadata_from_boundary.get("messages_to_keep_ids"),
                metadata_from_boundary.get("messagesToKeep"),
                metadata_from_boundary.get("preserved_message_ids"),
                metadata_from_boundary.get("preserved_segment_message_ids"),
            ]
        )
        preserved = (
            metadata_from_boundary.get("preserved_segment") or metadata_from_boundary.get("preservedSegment") or {}
        )
        if isinstance(preserved, dict):
            raw_values.append(preserved.get("message_ids") or preserved.get("messageIds"))
    message_ids: set[str] = set()
    for raw_ids in raw_values:
        if isinstance(raw_ids, list):
            message_ids.update(str(item).strip() for item in raw_ids if str(item).strip())
    return message_ids


def preserved_segment_with_tool_call_owners(
    prior_messages: list[dict[str, Any]],
    preserved_segment: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not preserved_segment:
        return []
    tool_call_owners: dict[str, dict[str, Any]] = {}
    for message in prior_messages:
        for tool_call_id in tool_call_ids(message):
            tool_call_owners[tool_call_id] = message

    result: list[dict[str, Any]] = []
    emitted_ids = {
        str(message.get("id") or "").strip() for message in preserved_segment if str(message.get("id") or "").strip()
    }
    for message in preserved_segment:
        tool_call_id = (
            str(message.get("tool_call_id") or "").strip() if str(message.get("role") or "") == "tool" else ""
        )
        owner = tool_call_owners.get(tool_call_id)
        owner_id = str((owner or {}).get("id") or "").strip()
        if owner and owner_id not in emitted_ids:
            result.append(copy.deepcopy(owner))
            emitted_ids.add(owner_id)
        result.append(copy.deepcopy(message))
    return result


def compact_metadata(boundary: dict[str, Any]) -> dict[str, Any]:
    metadata = boundary.get("metadata") or {}
    if not isinstance(metadata, dict):
        return {}
    metadata_from_boundary = metadata.get("compact_metadata") or metadata.get("compactMetadata") or {}
    if isinstance(metadata_from_boundary, dict):
        return dict(metadata_from_boundary)
    return {}


def redact_compact_metadata(compact_metadata_value: dict[str, Any]) -> dict[str, Any]:
    """Return compact metadata safe for API responses and telemetry."""

    redacted = copy.deepcopy(compact_metadata_value)
    preserved = redacted.get("preserved_segment") or redacted.get("preservedSegment")
    if isinstance(preserved, dict):
        raw_messages = preserved.pop("messages", [])
        if isinstance(raw_messages, list):
            preserved["message_count"] = len([message for message in raw_messages if isinstance(message, dict)])
        redacted["preserved_segment"] = preserved
        redacted.pop("preservedSegment", None)
    return redacted


def preserved_segment_messages(boundary: dict[str, Any]) -> list[dict[str, Any]]:
    metadata_from_boundary = compact_metadata(boundary)
    preserved = metadata_from_boundary.get("preserved_segment") or metadata_from_boundary.get("preservedSegment") or []
    raw_messages = preserved.get("messages") if isinstance(preserved, dict) else preserved
    if not isinstance(raw_messages, list):
        return []
    return [copy.deepcopy(message) for message in raw_messages if isinstance(message, dict)]


def expand_tool_pair_message_ids(messages: list[dict[str, Any]], ids: set[str]) -> set[str]:
    if not ids:
        return set()
    expanded = set(ids)
    id_by_tool_call: dict[str, str] = {}
    tool_call_owner_ids: dict[str, str] = {}
    for message in messages:
        message_id = str(message.get("id") or "").strip()
        for tool_call_id in tool_call_ids(message):
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


def tool_call_ids(message: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for tool_call in message.get("tool_calls") or []:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = str(tool_call.get("id") or "").strip()
        if tool_call_id:
            ids.add(tool_call_id)
    return ids
