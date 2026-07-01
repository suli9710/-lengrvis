from __future__ import annotations

import json
from typing import Any

from app.core.schemas import AgentMessage, OpenAIMessageRole
from app.policy.redaction import REDACTED, contains_sensitive_key, redact_public_text


def llm_safe_agent_message(
    message: AgentMessage,
    *,
    include_legacy: bool = False,
    redact_user_content: bool = False,
) -> dict[str, Any]:
    payload = message.to_openai_dict(include_legacy=include_legacy)
    for key in ("metadata", "structured_payload"):
        if isinstance(payload.get(key), dict):
            payload[key] = _llm_safe_value(payload[key])
    if redact_user_content or payload.get("role") != OpenAIMessageRole.USER.value:
        payload["content"] = _llm_safe_text(str(payload.get("content") or ""))
    if payload.get("tool_calls"):
        payload["tool_calls"] = _llm_safe_tool_calls(payload.get("tool_calls") or [])
    return payload


def _llm_safe_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    safe_calls: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        safe_call = dict(tool_call)
        function = dict(safe_call.get("function") or {})
        if "arguments" in function:
            function["arguments"] = _llm_safe_tool_arguments(function.get("arguments"))
        safe_call["function"] = function
        safe_calls.append(safe_call)
    return safe_calls


def _llm_safe_tool_arguments(arguments: Any) -> str:
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            return _llm_safe_text(arguments)
        return json.dumps(_llm_safe_value(parsed), ensure_ascii=False)
    return json.dumps(_llm_safe_value(arguments), ensure_ascii=False)


def _llm_safe_value(value: Any, *, key: str = "") -> Any:
    if key and contains_sensitive_key(key):
        return REDACTED if value is not None else value
    if isinstance(value, dict):
        return {str(item_key): _llm_safe_value(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_llm_safe_value(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_llm_safe_value(item, key=key) for item in value]
    if isinstance(value, set):
        return [_llm_safe_value(item, key=key) for item in sorted(value, key=str)]
    if isinstance(value, str):
        return _llm_safe_text(value)
    return value


def _llm_safe_text(text: str) -> str:
    return redact_public_text(str(text or ""), redact_generic_tokens=False)
