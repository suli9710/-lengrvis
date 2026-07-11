from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.policy.redaction import redact_public_text, redact_value

_TOOL_INPUT_CONTENT_KEYS = frozenset(
    {
        "content",
        "input",
        "new_source",
        "new_string",
        "old_source",
        "old_string",
    }
)
_TOOL_INPUT_COMMAND_KEYS = frozenset({"command"})
_TOOL_INPUT_PATH_KEYS = frozenset(
    {
        "file_path",
        "notebook_path",
        "path",
        "source_path",
        "target_path",
    }
)
_INLINE_TOOL_INPUT_KEYS = tuple(
    sorted(_TOOL_INPUT_CONTENT_KEYS | _TOOL_INPUT_COMMAND_KEYS | _TOOL_INPUT_PATH_KEYS, key=len, reverse=True)
)
_INLINE_TOOL_INPUT_PATTERN = re.compile(
    r"(?i)(?P<prefix>(?:^|[\s,;{\[])(?P<quote>[\"']?)(?P<key>"
    + "|".join(re.escape(key) for key in _INLINE_TOOL_INPUT_KEYS)
    + r")(?P=quote)\s*[:=]\s*)(?P<value>\"[^\"]*\"|'[^']*'|[^,;\n\r}]+)"
)


def _short_json(value: Any, *, limit: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return text[:limit]


def _public_lengrvis_code_text(value: Any, *, limit: int | None = None) -> str:
    redacted = redact_public_text(str(redact_value(str(value or "")) or ""))
    return redacted[:limit] if limit is not None else redacted


def _public_lengrvis_code_final_text(value: Any) -> str:
    return "[REDACTED_LENGRVIS_CODE_FINAL_TEXT]" if str(value or "").strip() else ""


def _public_lengrvis_code_json(value: Any, *, limit: int = 500) -> str:
    return _public_lengrvis_code_text(_short_json(value, limit=limit * 2), limit=limit)


def _public_lengrvis_code_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted = redact_value(dict(value))
        if not isinstance(redacted, Mapping):
            return _public_lengrvis_code_text(redacted)
        return {str(key): _public_lengrvis_code_value(item) for key, item in redacted.items()}
    if isinstance(value, list):
        return [_public_lengrvis_code_value(item) for item in value]
    if isinstance(value, tuple):
        return [_public_lengrvis_code_value(item) for item in value]
    if isinstance(value, set):
        return [_public_lengrvis_code_value(item) for item in sorted(value, key=str)]
    if isinstance(value, str):
        return _public_lengrvis_code_text(value)
    return value


def _public_lengrvis_code_tool_input_text(value: Any, *, limit: int | None = None) -> str:
    redacted = _redact_inline_tool_input(_public_lengrvis_code_text(value))
    return redacted[:limit] if limit is not None else redacted


def _public_lengrvis_code_tool_input_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _public_lengrvis_code_tool_input_keyed_value(str(key), item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_lengrvis_code_tool_input_value(item) for item in value]
    if isinstance(value, tuple):
        return [_public_lengrvis_code_tool_input_value(item) for item in value]
    if isinstance(value, set):
        return [_public_lengrvis_code_tool_input_value(item) for item in sorted(value, key=str)]
    if isinstance(value, str):
        return _public_lengrvis_code_tool_input_text(value)
    return value


def _public_lengrvis_code_tool_event(event: Any) -> Any:
    if not isinstance(event, Mapping):
        return _public_lengrvis_code_tool_input_value(event)
    return {str(key): _public_lengrvis_code_tool_input_keyed_value(str(key), item) for key, item in event.items()}


def _public_lengrvis_code_command(command: Sequence[Any]) -> list[str]:
    return ["[REDACTED_COMMAND]"] if command else []


def _public_lengrvis_code_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    if not result.get("is_error"):
        safe = _public_lengrvis_code_value(result)
        if not isinstance(safe, dict):
            return {}
        if isinstance(result.get("result"), str):
            safe["result"] = _public_lengrvis_code_final_text(result.get("result"))
        if isinstance(safe.get("permission_denials"), list):
            safe["permission_denials"] = _public_lengrvis_code_value(safe["permission_denials"])
        return safe
    return _public_lengrvis_code_value(result)


def _redacted_command(command: Sequence[str]) -> list[str]:
    redacted: list[str] = []
    for token in command:
        text = str(token)
        if text.startswith("sk-"):
            redacted.append("[REDACTED]")
        else:
            redacted.append(text)
    return redacted


def _public_lengrvis_code_tool_input_keyed_value(key: str, value: Any) -> Any:
    normalized = _normalized_tool_input_key(key)
    if _is_semantic_tool_input_key(normalized):
        if normalized == "input" and isinstance(value, Mapping):
            return _public_lengrvis_code_tool_input_value(value)
        return _tool_input_marker(normalized)
    return _public_lengrvis_code_tool_input_value(value)


def _normalized_tool_input_key(key: str) -> str:
    return key.replace("-", "_").casefold()


def _is_semantic_tool_input_key(normalized_key: str) -> bool:
    return (
        normalized_key in _TOOL_INPUT_CONTENT_KEYS
        or normalized_key in _TOOL_INPUT_COMMAND_KEYS
        or normalized_key in _TOOL_INPUT_PATH_KEYS
        or normalized_key.endswith("_path")
    )


def _tool_input_marker(normalized_key: str) -> str:
    if normalized_key in _TOOL_INPUT_COMMAND_KEYS:
        return "[REDACTED_COMMAND]"
    if normalized_key in _TOOL_INPUT_PATH_KEYS or normalized_key.endswith("_path"):
        return "[REDACTED_PATH]"
    if normalized_key == "input":
        return "[REDACTED_INPUT]"
    return "[REDACTED_CONTENT]"


def _redact_inline_tool_input(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return f"{match.group('prefix')}{_tool_input_marker(_normalized_tool_input_key(match.group('key')))}"

    return _INLINE_TOOL_INPUT_PATTERN.sub(replace, text)
