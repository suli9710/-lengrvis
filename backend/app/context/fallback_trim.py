from __future__ import annotations

import copy
from typing import Any

from app.context.compact_boundaries import is_compact_boundary, tool_call_ids
from app.context.tokens import count_messages_tokens


def fallback_target_tokens(effective_window: int, manual_buffer_tokens: int) -> int:
    return max(1, max(1, int(effective_window)) - max(0, int(manual_buffer_tokens)))


def trim_oldest_unprotected_blocks(messages: list[dict[str, Any]], target_tokens: int) -> list[dict[str, Any]]:
    blocks = _message_blocks(messages)
    protected_indexes = _protected_block_indexes(blocks)
    while count_messages_tokens(_flatten_blocks(blocks)) > target_tokens:
        remove_index = next((index for index in range(len(blocks)) if index not in protected_indexes), None)
        if remove_index is None:
            break
        blocks.pop(remove_index)
        protected_indexes = {
            index - 1 if index > remove_index else index for index in protected_indexes if index != remove_index
        }
    return _flatten_blocks(blocks)


def _message_blocks(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    blocks: list[list[dict[str, Any]]] = []
    index = 0
    while index < len(messages):
        message = copy.deepcopy(messages[index])
        block = [message]
        if tool_call_ids(message):
            index += 1
            while index < len(messages) and str(messages[index].get("role") or "") == "tool":
                block.append(copy.deepcopy(messages[index]))
                index += 1
            blocks.append(block)
            continue
        blocks.append(block)
        index += 1
    return blocks


def _protected_block_indexes(blocks: list[list[dict[str, Any]]]) -> set[int]:
    protected: set[int] = set()
    latest_boundary_index: int | None = None
    latest_user_index: int | None = None
    latest_tool_block_index: int | None = None
    for index, block in enumerate(blocks):
        first = block[0] if block else {}
        if str(first.get("role") or "") in {"system", "developer"}:
            protected.add(index)
        if any(is_compact_boundary(message) for message in block):
            latest_boundary_index = index
        if str(first.get("role") or "") == "user":
            latest_user_index = index
        if _block_has_complete_tool_pair(block):
            latest_tool_block_index = index
    if latest_boundary_index is not None:
        protected.add(latest_boundary_index)
    if latest_user_index is not None:
        protected.add(latest_user_index)
    if latest_tool_block_index is not None and (
        latest_boundary_index is None or latest_tool_block_index > latest_boundary_index
    ):
        protected.add(latest_tool_block_index)
    return protected


def _block_has_complete_tool_pair(block: list[dict[str, Any]]) -> bool:
    if not block:
        return False
    call_ids = tool_call_ids(block[0])
    if not call_ids:
        return False
    result_ids = {
        str(message.get("tool_call_id") or "").strip()
        for message in block[1:]
        if str(message.get("role") or "") == "tool" and str(message.get("tool_call_id") or "").strip()
    }
    return bool(call_ids) and call_ids.issubset(result_ids)


def _flatten_blocks(blocks: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for block in blocks for message in block]
