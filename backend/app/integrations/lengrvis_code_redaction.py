from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from app.policy.redaction import redact_public_text, redact_value


def _short_json(value: Any, *, limit: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return text[:limit]


def _public_lengrvis_code_text(value: Any, *, limit: int | None = None) -> str:
    redacted = redact_public_text(str(redact_value(str(value or "")) or ""))
    return redacted[:limit] if limit is not None else redacted


def _public_lengrvis_code_json(value: Any, *, limit: int = 500) -> str:
    return _public_lengrvis_code_text(_short_json(value, limit=limit * 2), limit=limit)


def _public_lengrvis_code_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _public_lengrvis_code_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_lengrvis_code_value(item) for item in value]
    if isinstance(value, tuple):
        return [_public_lengrvis_code_value(item) for item in value]
    if isinstance(value, set):
        return [_public_lengrvis_code_value(item) for item in sorted(value, key=str)]
    if isinstance(value, str):
        return _public_lengrvis_code_text(value)
    return value


def _public_lengrvis_code_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    if not result.get("is_error"):
        safe = dict(result)
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
