from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.integrations.lengrvis_code_constants import (
    ERROR_CANCELLED,
    LENGRVIS_CODE_ADAPTER_NAME,
    LENGRVIS_CODE_DISPLAY_NAME,
    MAX_ADAPTER_EVENTS,
)
from app.integrations.lengrvis_code_errors import classify_lengrvis_code_error
from app.integrations.lengrvis_code_redaction import (
    _public_lengrvis_code_command,
    _public_lengrvis_code_final_text,
    _public_lengrvis_code_json,
    _public_lengrvis_code_result,
    _public_lengrvis_code_text,
    _public_lengrvis_code_tool_event,
    _public_lengrvis_code_tool_input_text,
    _public_lengrvis_code_tool_input_value,
    _public_lengrvis_code_value,
    _short_json,
)


class LengrvisCodeEventSummaryLike(Protocol):
    events: list[dict[str, Any]]
    assistant_text: list[str]
    tool_events: list[dict[str, Any]]
    system_events: list[dict[str, Any]]
    invalid_lines: list[str]
    result: dict[str, Any] | None
    stderr: str
    returncode: int | None
    cancelled: bool
    command: list[str]
    launch_error: str
    runtime_health: dict[str, Any]

    @property
    def final_text(self) -> str: ...

    @property
    def is_error(self) -> bool: ...

    @property
    def usage(self) -> dict[str, Any]: ...

    @property
    def permission_denials(self) -> list[Any]: ...


def _record_event(summary: LengrvisCodeEventSummaryLike, event: dict[str, Any]) -> None:
    summary.events.append(event)
    event_type = str(event.get("type") or "")
    if event_type == "assistant":
        summary.assistant_text.extend(_assistant_text(event))
        summary.tool_events.extend(_assistant_tool_uses(event))
    elif event_type == "streamlined_text":
        text = event.get("text")
        if isinstance(text, str):
            summary.assistant_text.append(text)
    elif event_type == "streamlined_tool_use_summary":
        summary.tool_events.append(event)
    elif event_type == "system":
        summary.system_events.append(event)
    elif event_type == "result":
        summary.result = event


def _assistant_text(event: Mapping[str, Any]) -> list[str]:
    message = event.get("message")
    if not isinstance(message, Mapping):
        return []
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str):
            texts.append(str(block["text"]))
    return texts


def _assistant_tool_uses(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, Mapping):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    tools: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "tool_use":
            tools.append(dict(block))
    return tools


def _summary_message(summary: LengrvisCodeEventSummaryLike) -> str:
    if summary.cancelled:
        return f"{LENGRVIS_CODE_DISPLAY_NAME} run was cancelled."
    if summary.is_error:
        return _error_reason(summary)
    if summary.final_text:
        return f"{LENGRVIS_CODE_DISPLAY_NAME} completed with redacted final text."
    return f"{LENGRVIS_CODE_DISPLAY_NAME} emitted {len(summary.events)} stream-json event(s)."


def _error_reason(summary: LengrvisCodeEventSummaryLike) -> str:
    if summary.launch_error:
        return f"{LENGRVIS_CODE_DISPLAY_NAME} launch failure: {_public_lengrvis_code_text(summary.launch_error)}"
    if summary.permission_denials:
        denials = _public_lengrvis_code_json(summary.permission_denials)
        return f"{LENGRVIS_CODE_DISPLAY_NAME} permission denied: {denials}"
    if summary.result and isinstance(summary.result.get("errors"), list) and summary.result["errors"]:
        return "; ".join(_public_lengrvis_code_text(item) for item in summary.result["errors"])
    if summary.result and isinstance(summary.result.get("subtype"), str):
        return f"{LENGRVIS_CODE_DISPLAY_NAME} result: {summary.result['subtype']}"
    if summary.stderr.strip():
        return _public_lengrvis_code_text(summary.stderr.strip(), limit=500)
    if summary.returncode not in {None, 0}:
        return f"{LENGRVIS_CODE_DISPLAY_NAME} exited with code {summary.returncode}."
    if summary.invalid_lines:
        return f"{LENGRVIS_CODE_DISPLAY_NAME} emitted malformed stream-json line(s): {len(summary.invalid_lines)}."
    return f"{LENGRVIS_CODE_DISPLAY_NAME} run failed."


def _summary_payload(summary: LengrvisCodeEventSummaryLike) -> dict[str, Any]:
    adapter_events = _adapter_events(summary)
    lengrvis_events = _lengrvis_events(summary, adapter_events)
    error_classification = classify_lengrvis_code_error(summary)
    payload: dict[str, Any] = {
        "ok": error_classification is None,
        "cancelled": summary.cancelled,
        "display_name": LENGRVIS_CODE_DISPLAY_NAME,
        "adapter_name": LENGRVIS_CODE_ADAPTER_NAME,
        "error_classification": error_classification,
        "returncode": summary.returncode,
        "event_count": len(summary.events),
        "assistant_text": _public_lengrvis_code_text(summary.final_text)
        if summary.is_error
        else _public_lengrvis_code_final_text(summary.final_text),
        "tool_events": [_public_lengrvis_code_tool_event(event) for event in summary.tool_events],
        "system_events": _public_lengrvis_code_value(summary.system_events),
        "result": _public_lengrvis_code_result(summary.result),
        "usage": summary.usage,
        "permission_denials": _public_lengrvis_code_value(summary.permission_denials),
        "invalid_line_count": len(summary.invalid_lines),
        "diagnostics": _diagnostics(summary),
        "adapter_events": adapter_events,
        "lengrvis_events": lengrvis_events,
        "command": _public_lengrvis_code_command(summary.command),
        "runtime_health": _public_lengrvis_code_value(summary.runtime_health),
        "stderr_diagnostics": _stderr_diagnostics(summary),
    }
    if summary.permission_denials:
        payload["awaiting_write_approval" if _backend_approval_required(summary) else "permission_denied"] = True
    if summary.launch_error:
        payload["launch_error"] = _public_lengrvis_code_text(summary.launch_error)
    if summary.stderr:
        payload["stderr"] = _public_lengrvis_code_text(summary.stderr[-4000:])
    if summary.invalid_lines:
        payload["invalid_lines"] = [_public_lengrvis_code_text(line, limit=500) for line in summary.invalid_lines[:10]]
    return payload


def _diagnostics(summary: LengrvisCodeEventSummaryLike) -> list[str]:
    diagnostics: list[str] = []
    if summary.launch_error:
        launch_error = _public_lengrvis_code_text(summary.launch_error)
        diagnostics.append(f"{LENGRVIS_CODE_DISPLAY_NAME} launch failure: {launch_error}")
    health_diagnostic = summary.runtime_health.get("diagnostic") if summary.runtime_health else ""
    if isinstance(health_diagnostic, str) and health_diagnostic:
        diagnostics.append(_public_lengrvis_code_text(health_diagnostic))
    if summary.stderr.strip():
        stderr = _public_lengrvis_code_text(summary.stderr.strip(), limit=500)
        diagnostics.append(f"{LENGRVIS_CODE_DISPLAY_NAME} stderr: {stderr}")
    if summary.invalid_lines:
        diagnostics.append(f"Malformed {LENGRVIS_CODE_DISPLAY_NAME} stream-json lines: {len(summary.invalid_lines)}")
    if summary.returncode not in {None, 0}:
        diagnostics.append(f"{LENGRVIS_CODE_DISPLAY_NAME} exited with code {summary.returncode}.")
    if summary.permission_denials:
        diagnostics.append(
            f"{LENGRVIS_CODE_DISPLAY_NAME} permission denied: {_public_lengrvis_code_json(summary.permission_denials)}"
        )
    if summary.result and isinstance(summary.result.get("errors"), list):
        diagnostics.extend(_public_lengrvis_code_text(item) for item in summary.result["errors"])
    return diagnostics


def _adapter_events(summary: LengrvisCodeEventSummaryLike) -> list[dict[str, Any]]:
    start = max(0, len(summary.events) - MAX_ADAPTER_EVENTS)
    events: list[dict[str, Any]] = []
    for offset, event in enumerate(summary.events[start:], start=start + 1):
        event_type = str(event.get("type") or "unknown")
        message = _event_summary(event)
        events.append(
            {
                "sequence": offset,
                "event_type": event_type,
                "summary": message,
                "lengrvis_events": _lengrvis_events_for_source_event(
                    event,
                    message,
                    source_event_index=offset,
                    summary=summary,
                ),
            }
        )
    return events


def _lengrvis_events(
    summary: LengrvisCodeEventSummaryLike, adapter_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for adapter_event in adapter_events:
        event_payload = adapter_event.get("lengrvis_event")
        if isinstance(event_payload, dict):
            events.append(event_payload)
        for event in adapter_event.get("lengrvis_events") or []:
            if isinstance(event, dict):
                events.append(event)
    events.append(_terminal_lengrvis_event(summary))
    return events


def _event_summary(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("type") or "unknown")
    if event_type == "system":
        subtype = event.get("subtype")
        if subtype == "init":
            tools = event.get("tools") if isinstance(event.get("tools"), list) else []
            return f"{LENGRVIS_CODE_DISPLAY_NAME} initialized with {len(tools)} tools."
        return f"{LENGRVIS_CODE_DISPLAY_NAME} system event: {subtype or 'unknown'}."
    if event_type == "assistant":
        texts = _assistant_text(event)
        if texts:
            return "\n".join(texts).strip()[:500]
        tools = _assistant_tool_names(event)
        if tools:
            return f"{LENGRVIS_CODE_DISPLAY_NAME} requested tool(s): {', '.join(tools)}."
        return f"{LENGRVIS_CODE_DISPLAY_NAME} assistant message."
    if event_type == "result":
        if isinstance(event.get("errors"), list) and event["errors"]:
            return "; ".join(_public_lengrvis_code_text(item) for item in event["errors"])[:500]
        return f"{LENGRVIS_CODE_DISPLAY_NAME} result: {event.get('subtype') or 'unknown'}."
    if event_type in {"streamlined_text", "text"} and isinstance(event.get("text"), str):
        return str(event["text"]).strip()[:500]
    if event_type in {"streamlined_tool_use_summary", "tool_use_summary"}:
        return _tool_use_summary_message(event)
    if event_type == "user":
        tool_results = _user_tool_results(event)
        if tool_results:
            names = [item.get("tool_name") or item.get("tool_use_id") or "tool" for item in tool_results]
            return f"{LENGRVIS_CODE_DISPLAY_NAME} tool result(s): {', '.join(str(name) for name in names)}."
        return f"{LENGRVIS_CODE_DISPLAY_NAME} user event."
    return f"{LENGRVIS_CODE_DISPLAY_NAME} event: {event_type}."


def _lengrvis_events_for_source_event(
    event: Mapping[str, Any],
    message: str,
    *,
    source_event_index: int,
    summary: LengrvisCodeEventSummaryLike,
) -> list[dict[str, Any]]:
    event_type = str(event.get("type") or "unknown")
    base = _base_event_payload(
        source_event_index=source_event_index,
        source_event_type=event_type,
        summary=summary,
    )
    if event_type == "assistant":
        events: list[dict[str, Any]] = []
        texts = [text for text in _assistant_text(event) if text.strip()]
        for text in texts:
            events.append(
                {
                    "name": "agent.message",
                    "payload": {
                        **base,
                        "agent": LENGRVIS_CODE_DISPLAY_NAME,
                        "agent_display_name": LENGRVIS_CODE_DISPLAY_NAME,
                        "message": text.strip(),
                        "source": LENGRVIS_CODE_ADAPTER_NAME,
                    },
                }
            )
        for tool in _assistant_tool_uses(event):
            tool_name = str(tool.get("name") or "unknown")
            tool_input_summary = _tool_input_summary(tool.get("input"))
            payload = {
                **base,
                "tool_name": tool_name,
                "adapter_tool_name": LENGRVIS_CODE_ADAPTER_NAME,
                "status": "proposed",
                "message": f"{LENGRVIS_CODE_DISPLAY_NAME} proposed {tool_name}.",
                "tool_input_summary": tool_input_summary,
                "tool_use_id": tool.get("id"),
            }
            events.append({"name": "tool.proposed", "payload": payload})
            events.append(
                {
                    "name": "tool.progress",
                    "payload": {
                        **payload,
                        "status": "running",
                        "message": f"{LENGRVIS_CODE_DISPLAY_NAME} running {tool_name}.",
                    },
                }
            )
        if events:
            return events
        return [
            {
                "name": "agent.message",
                "payload": {
                    **base,
                    "agent": LENGRVIS_CODE_DISPLAY_NAME,
                    "agent_display_name": LENGRVIS_CODE_DISPLAY_NAME,
                    "message": message,
                    "source": LENGRVIS_CODE_ADAPTER_NAME,
                },
            }
        ]
    if event_type in {"streamlined_text", "text"}:
        text = str(event.get("text") or "").strip()
        if text:
            return [
                {
                    "name": "agent.message",
                    "payload": {
                        **base,
                        "agent": LENGRVIS_CODE_DISPLAY_NAME,
                        "agent_display_name": LENGRVIS_CODE_DISPLAY_NAME,
                        "message": text,
                        "source": LENGRVIS_CODE_ADAPTER_NAME,
                    },
                }
            ]
    if event_type in {"streamlined_tool_use_summary", "tool_use_summary"}:
        return [
            {
                "name": "tool.progress",
                "payload": {
                    **base,
                    "tool_name": _tool_name_from_summary_event(event),
                    "adapter_tool_name": LENGRVIS_CODE_ADAPTER_NAME,
                    "status": "running",
                    "message": _tool_use_summary_message(event),
                    "tool_input_summary": _tool_input_summary(event.get("summary")),
                    "preceding_tool_use_ids": event.get("preceding_tool_use_ids"),
                },
            }
        ]
    if event_type == "user":
        events = []
        for tool_result in _user_tool_results(event):
            tool_name = str(tool_result.get("tool_name") or LENGRVIS_CODE_ADAPTER_NAME)
            events.append(
                {
                    "name": "tool.result",
                    "payload": {
                        **base,
                        "tool_name": tool_name,
                        "adapter_tool_name": LENGRVIS_CODE_ADAPTER_NAME,
                        "status": "failed" if tool_result.get("is_error") else "completed",
                        "message": _tool_result_message(tool_result),
                        "tool_use_id": tool_result.get("tool_use_id"),
                        "output": _tool_result_output_payload(tool_result),
                    },
                }
            )
        return events
    if event_type == "result":
        status = _terminal_status(summary)
        return [
            {
                "name": "tool.result",
                "payload": {
                    **base,
                    "tool_name": LENGRVIS_CODE_ADAPTER_NAME,
                    "status": status,
                    "message": message,
                    "output": _result_output_payload(event, summary),
                },
            }
        ]
    if event_type == "system":
        return [
            {
                "name": "tool.progress",
                "payload": {
                    **base,
                    "tool_name": LENGRVIS_CODE_ADAPTER_NAME,
                    "status": "running",
                    "message": message,
                    "system_subtype": event.get("subtype"),
                },
            }
        ]
    return [
        {
            "name": "tool.progress",
            "payload": {
                **base,
                "tool_name": LENGRVIS_CODE_ADAPTER_NAME,
                "status": "running",
                "message": message,
                "event_type": event_type,
            },
        }
    ]


def _terminal_lengrvis_event(summary: LengrvisCodeEventSummaryLike) -> dict[str, Any]:
    classification = classify_lengrvis_code_error(summary)
    if classification == ERROR_CANCELLED:
        name = "run.cancelled"
    elif classification is None:
        name = "run.completed"
    else:
        name = "run.failed"
    return {
        "name": name,
        "payload": {
            **_base_event_payload(
                source_event_index=len(summary.events) + 1,
                source_event_type="adapter_terminal",
                summary=summary,
            ),
            "tool_name": LENGRVIS_CODE_ADAPTER_NAME,
            "status": _terminal_status(summary),
            "message": _summary_message(summary),
            "error_classification": classification,
            "returncode": summary.returncode,
            "cancelled": summary.cancelled,
        },
    }


def _base_event_payload(
    *,
    source_event_index: int,
    source_event_type: str,
    summary: LengrvisCodeEventSummaryLike,
) -> dict[str, Any]:
    return {
        "source_event_index": source_event_index,
        "source_event_type": source_event_type,
        "adapter_name": LENGRVIS_CODE_ADAPTER_NAME,
        "adapter_display_name": LENGRVIS_CODE_DISPLAY_NAME,
        "usage": summary.usage,
        "permission_denials": _public_lengrvis_code_value(summary.permission_denials),
        "stderr_diagnostics": _stderr_diagnostics(summary),
    }


def _stderr_diagnostics(summary: LengrvisCodeEventSummaryLike) -> list[str]:
    stderr = summary.stderr.strip()
    if not stderr:
        return []
    return [_public_lengrvis_code_text(line.strip(), limit=500) for line in stderr.splitlines() if line.strip()][:10]


def _tool_input_summary(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _public_lengrvis_code_tool_input_text(value, limit=500)
    if isinstance(value, Mapping):
        safe_value = _public_lengrvis_code_tool_input_value(value)
        parts: list[str] = []
        for key, item in list(safe_value.items())[:8]:
            text = str(item)
            if len(text) > 80:
                text = f"{text[:77]}..."
            parts.append(f"{key}={text}")
        return ", ".join(parts)[:500]
    if isinstance(value, list):
        safe_value = _public_lengrvis_code_tool_input_value(value)
        return f"{len(value)} item(s): {_short_json(safe_value[:5], limit=420)}"
    return _public_lengrvis_code_tool_input_text(value, limit=500)


def _tool_name_from_summary_event(event: Mapping[str, Any]) -> str:
    for key in ("tool_name", "name"):
        value = event.get(key)
        if value:
            return str(value)
    summary = event.get("summary")
    if isinstance(summary, Mapping):
        for key in ("tool_name", "name"):
            value = summary.get(key)
            if value:
                return str(value)
    return LENGRVIS_CODE_ADAPTER_NAME


def _tool_use_summary_message(event: Mapping[str, Any]) -> str:
    summary = event.get("summary")
    if isinstance(summary, str) and summary.strip():
        return _public_lengrvis_code_tool_input_text(summary.strip(), limit=500)
    if isinstance(summary, Mapping):
        for key in ("summary", "message", "description"):
            value = summary.get(key)
            if isinstance(value, str) and value.strip():
                return _public_lengrvis_code_tool_input_text(value.strip(), limit=500)
        return _tool_input_summary(summary)
    return f"{LENGRVIS_CODE_DISPLAY_NAME} tool progress."


def _user_tool_results(event: Mapping[str, Any]) -> list[dict[str, Any]]:
    message = event.get("message")
    if not isinstance(message, Mapping):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    results: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == "tool_result":
            results.append(dict(block))
    return results


def _tool_result_message(tool_result: Mapping[str, Any]) -> str:
    content = tool_result.get("content")
    descriptor = _tool_result_content_descriptor(content)
    if tool_result.get("is_error"):
        return f"{LENGRVIS_CODE_DISPLAY_NAME} tool result failed{descriptor}."
    return f"{LENGRVIS_CODE_DISPLAY_NAME} tool result completed{descriptor}."


def _tool_result_content_descriptor(content: Any) -> str:
    if isinstance(content, str) and content.strip():
        return " with redacted text output"
    if isinstance(content, list):
        return f" with {len(content)} redacted output item(s)"
    if content is None:
        return ""
    return " with redacted structured output"


def _tool_result_output_payload(tool_result: Mapping[str, Any]) -> dict[str, Any]:
    content = tool_result.get("content")
    payload: dict[str, Any] = {
        "redacted": True,
        "is_error": bool(tool_result.get("is_error")),
        "content_type": type(content).__name__ if content is not None else "none",
    }
    if isinstance(content, list):
        payload["item_count"] = len(content)
    elif isinstance(content, str):
        payload["char_count"] = len(content)
    return payload


def _terminal_status(summary: LengrvisCodeEventSummaryLike) -> str:
    classification = classify_lengrvis_code_error(summary)
    if classification == ERROR_CANCELLED:
        return "cancelled"
    if classification is None:
        return "completed"
    return "failed"


def _result_output_payload(event: Mapping[str, Any], summary: LengrvisCodeEventSummaryLike) -> dict[str, Any]:
    payload = {
        "result": _public_lengrvis_code_text(event.get("result"))
        if event.get("is_error")
        else _public_lengrvis_code_final_text(event.get("result")),
        "subtype": event.get("subtype"),
        "is_error": bool(event.get("is_error")),
        "errors": _public_lengrvis_code_value(event.get("errors")) if isinstance(event.get("errors"), list) else [],
        "usage": summary.usage,
        "permission_denials": _public_lengrvis_code_value(summary.permission_denials),
        "error_classification": classify_lengrvis_code_error(summary),
        "returncode": summary.returncode,
        "stderr_diagnostics": _stderr_diagnostics(summary),
    }
    if summary.permission_denials:
        payload["awaiting_write_approval" if _backend_approval_required(summary) else "permission_denied"] = True
    return payload


def _backend_approval_required(summary: LengrvisCodeEventSummaryLike) -> bool:
    return isinstance(summary.result, dict) and summary.result.get("backend_approval_required") is True


def _assistant_tool_names(event: Mapping[str, Any]) -> list[str]:
    return [str(tool.get("name")) for tool in _assistant_tool_uses(event) if tool.get("name")]
