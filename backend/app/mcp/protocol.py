"""Strict JSON-RPC, SSE event, URI, and JSON-schema codecs for MCP."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
JSONRPC_VERSION = "2.0"


@dataclass(frozen=True, slots=True)
class _SSEEvent:
    message: dict[str, Any] | None = None
    event_id: str | None = None
    retry_milliseconds: int | None = None


def _decode_json_response(response: httpx.Response, *, expected_id: Any = None) -> dict[str, Any]:
    return _select_jsonrpc_response(
        _decode_json_messages(response),
        expected_id=expected_id,
    )


def _decode_json_messages(response: httpx.Response) -> list[dict[str, Any]]:
    raw = response.content
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("MCP response exceeded the size limit")
    if not raw:
        return [{}]
    content_type = response.headers.get("content-type", "").casefold()
    if "text/event-stream" in content_type:
        candidates: list[dict[str, Any]] = []
        for block in raw.decode("utf-8").replace("\r\n", "\n").split("\n\n"):
            parsed = _decode_sse_block(block)
            if parsed is not None:
                candidates.append(parsed)
        if not candidates:
            raise ValueError("MCP SSE response contained no JSON-RPC message")
        return candidates
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("MCP response must be a JSON object")
    return [parsed]


def _decode_sse_block(block: str) -> dict[str, Any] | None:
    return _decode_sse_event(block).message


def _decode_sse_event(block: str) -> _SSEEvent:
    data_lines: list[str] = []
    event_id: str | None = None
    retry_milliseconds: int | None = None
    for line in block.splitlines():
        if not line or line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            value = ""
        elif value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
        elif field == "id":
            # Last-Event-ID is copied into an HTTP header on resume.  Keep the
            # interoperable visible-ASCII subset and reject oversized/control
            # values instead of trusting the HTTP library to sanitize them.
            if value == "":
                event_id = ""
            elif len(value) <= 1024 and all(0x20 <= ord(char) <= 0x7E for char in value):
                event_id = value
        elif field == "retry" and value.isascii() and value.isdigit():
            retry_milliseconds = int(value)
    if not data_lines or not any(data_lines):
        return _SSEEvent(
            event_id=event_id,
            retry_milliseconds=retry_milliseconds,
        )
    try:
        parsed = json.loads("\n".join(data_lines))
    except json.JSONDecodeError:
        parsed = None
    return _SSEEvent(
        message=parsed if isinstance(parsed, dict) else None,
        event_id=event_id,
        retry_milliseconds=retry_milliseconds,
    )


def _select_jsonrpc_response(
    messages: list[dict[str, Any]],
    *,
    expected_id: Any,
) -> dict[str, Any]:
    if expected_id is not None:
        for candidate in messages:
            if candidate.get("id") == expected_id:
                return candidate
        raise ValueError("MCP response id did not match the request")
    return messages[-1] if messages else {}


def _canonical_mcp_resource(value: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid MCP resource URI") from exc
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if scheme not in {"http", "https"} or not host or parsed.fragment:
        raise ValueError("invalid MCP resource URI")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("MCP resource URI must not contain userinfo")
    default_port = 443 if scheme == "https" else 80
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    path = parsed.path or ""
    if path == "/":
        path = ""
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _validate_jsonrpc_envelope(data: Any, *, expected_id: Any) -> str:
    if not isinstance(data, dict):
        return "MCP response must be a JSON object"
    # Internal transport errors are deliberately represented without a JSON-RPC
    # envelope and are already safe for callers to surface as an inline error.
    if "jsonrpc" not in data and isinstance(data.get("error"), dict) and data["error"].get("type"):
        return ""
    if data.get("jsonrpc") != JSONRPC_VERSION:
        return "MCP response omitted jsonrpc=2.0"
    if expected_id is not None and data.get("id") != expected_id:
        return "MCP response id did not match the request"
    has_result = "result" in data
    has_error = "error" in data
    if has_result == has_error:
        return "MCP response must contain exactly one result or error"
    if has_error:
        error = data.get("error")
        if not isinstance(error, dict) or not isinstance(error.get("code"), int) or isinstance(error.get("code"), bool):
            return "MCP JSON-RPC error object is invalid"
        if not isinstance(error.get("message"), str) or not error.get("message"):
            return "MCP JSON-RPC error message is invalid"
    return ""


def _next_page_cursor(result: dict[str, Any], seen: set[str]) -> tuple[str | None, str]:
    if "nextCursor" not in result or result.get("nextCursor") is None:
        return None, ""
    raw = result.get("nextCursor")
    if not isinstance(raw, str) or not raw.strip():
        return None, "returned an invalid nextCursor"
    cursor = raw.strip()
    if len(cursor) > 4096 or any(ord(char) < 0x20 or ord(char) == 0x7F for char in cursor):
        return None, "returned an invalid nextCursor"
    if cursor in seen:
        return None, "repeated a pagination cursor"
    return cursor, ""


def _valid_session_id(value: str) -> bool:
    return bool(value) and len(value) <= 1024 and all(0x21 <= ord(char) <= 0x7E for char in value)


def _validate_tool_arguments(arguments: Any, schema: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "MCP tool arguments must be a JSON object"
    return _validate_json_schema(arguments, schema, label="input_schema")


def _validate_json_schema(value: Any, schema: dict[str, Any], *, label: str) -> str:
    if not schema:
        return ""
    if not isinstance(schema, dict):
        return f"MCP tool {label} must be a JSON schema object"
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError
    except ImportError:  # pragma: no cover - jsonschema may be absent in minimal installs.
        return _validate_json_schema_lightweight(value, schema, label=label)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(value)
    except SchemaError as exc:
        return f"MCP tool {label} is invalid: {exc.message}"
    except ValidationError as exc:
        return f"MCP tool value did not match {label}: {exc.message}"
    return ""


def _validate_json_schema_lightweight(value: Any, schema: dict[str, Any], *, label: str) -> str:
    expected_type = schema.get("type")
    if expected_type and not _matches_json_schema_type(value, expected_type):
        return f"MCP tool value did not match {label}: wrong type"
    if isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = schema.get("required") if isinstance(schema.get("required"), list) else []
        for field in required:
            if isinstance(field, str) and field not in value:
                return f"MCP tool value did not match {label}: {field!r} is required"
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                return f"MCP tool value did not match {label}: unexpected fields: {', '.join(extra)}"
        for field, field_schema in properties.items():
            if field in value and isinstance(field_schema, dict):
                nested = _validate_json_schema_lightweight(value[field], field_schema, label=label)
                if nested:
                    return nested
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        return f"MCP tool value did not match {label}: value is not allowed"
    return ""


def _matches_json_schema_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_json_schema_type(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True
