from __future__ import annotations

import json
import re
from typing import Any

STRUCTURED_FAILURE_KINDS = frozenset(
    {
        "not_json",
        "schema_mismatch",
        "native_unsupported",
        "malformed_provider_response",
    }
)

_SECRET_PATTERN = re.compile(
    r"(sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|token=[A-Za-z0-9._-]{8,})",
    re.IGNORECASE,
)


class LLMApiResponseError(RuntimeError):
    """Raised when a provider returns a syntactically successful but invalid body."""


class LLMStructuredOutputError(LLMApiResponseError):
    """Raised when a provider cannot produce valid structured JSON."""

    def __init__(self, message: str, failure_kind: str) -> None:
        if failure_kind not in STRUCTURED_FAILURE_KINDS:
            failure_kind = "malformed_provider_response"
        self.failure_kind = failure_kind
        super().__init__(message)


def safe_structured_excerpt(content: str, *, max_chars: int = 1200) -> str:
    text = _SECRET_PATTERN.sub("[REDACTED]", str(content or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def parse_and_validate_structured_content(content: str, output_schema: dict[str, Any]) -> dict[str, Any]:
    payload = parse_structured_json(content)
    validate_structured_payload(payload, output_schema)
    if not isinstance(payload, dict):
        raise LLMStructuredOutputError(
            "LLM structured response did not match output schema: $ expected object.",
            "schema_mismatch",
        )
    return payload


def parse_structured_json(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as original:
        decoder = json.JSONDecoder()
        for index, char in enumerate(str(content or "")):
            if char not in "{[":
                continue
            try:
                payload, _ = decoder.raw_decode(str(content)[index:])
            except json.JSONDecodeError:
                continue
            return payload
        raise LLMStructuredOutputError("LLM structured response was not valid JSON.", "not_json") from original


def check_output_schema(output_schema: dict[str, Any]) -> None:
    if not isinstance(output_schema, dict):
        raise LLMStructuredOutputError("LLM output schema must be a JSON Schema object.", "malformed_provider_response")
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError:
        return
    try:
        Draft202012Validator.check_schema(output_schema)
    except SchemaError as exc:
        raise LLMStructuredOutputError(
            f"LLM output schema is invalid: {exc.message}",
            "malformed_provider_response",
        ) from exc


def validate_structured_payload(payload: Any, output_schema: dict[str, Any]) -> None:
    check_output_schema(output_schema)
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import ValidationError
    except ImportError:
        validate_structured_payload_lightweight(payload, output_schema)
        return

    try:
        Draft202012Validator(output_schema).validate(payload)
    except ValidationError as exc:
        raise LLMStructuredOutputError(
            f"LLM structured response did not match output schema: {_format_jsonschema_error(exc)}",
            "schema_mismatch",
        ) from exc


def validate_structured_payload_lightweight(payload: Any, schema: dict[str, Any], path: str = "$") -> None:
    if not isinstance(schema, dict):
        return
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_json_schema_type(payload, expected_type):
        raise _schema_validation_error(
            f"{path} expected {_format_expected_type(expected_type)}, got {_json_type_name(payload)}."
        )
    enum = schema.get("enum")
    if isinstance(enum, list) and payload not in enum:
        raise _schema_validation_error(f"{path} must be one of {enum!r}.")

    if _should_validate_object_keywords(payload, schema):
        _validate_object_payload(payload, schema, path)
    if _should_validate_array_keywords(payload, schema):
        _validate_array_payload(payload, schema, path)


def _should_validate_object_keywords(payload: Any, schema: dict[str, Any]) -> bool:
    schema_type = schema.get("type")
    return (
        schema_type == "object"
        or isinstance(payload, dict)
        and any(key in schema for key in ("required", "properties", "additionalProperties"))
    )


def _should_validate_array_keywords(payload: Any, schema: dict[str, Any]) -> bool:
    schema_type = schema.get("type")
    return schema_type == "array" or isinstance(payload, list) and "items" in schema


def _validate_object_payload(payload: Any, schema: dict[str, Any], path: str) -> None:
    if not isinstance(payload, dict):
        raise _schema_validation_error(f"{path} expected object, got {_json_type_name(payload)}.")

    required = schema.get("required") or []
    missing = [str(key) for key in required if str(key) not in payload]
    if missing:
        raise _schema_validation_error(f"{path} missing required field(s): {', '.join(missing)}.")

    properties = schema.get("properties") or {}
    if not isinstance(properties, dict):
        properties = {}
    for key, property_schema in properties.items():
        if key in payload:
            validate_structured_payload_lightweight(payload[key], property_schema, _join_schema_path(path, key))

    additional_properties = schema.get("additionalProperties", True)
    extra_keys = [key for key in payload if key not in properties]
    if additional_properties is False and extra_keys:
        extra = ", ".join(str(key) for key in extra_keys)
        raise _schema_validation_error(f"{path} included unexpected field(s): {extra}.")
    if isinstance(additional_properties, dict):
        for key in extra_keys:
            validate_structured_payload_lightweight(
                payload[key],
                additional_properties,
                _join_schema_path(path, key),
            )


def _validate_array_payload(payload: Any, schema: dict[str, Any], path: str) -> None:
    if not isinstance(payload, list):
        raise _schema_validation_error(f"{path} expected array, got {_json_type_name(payload)}.")
    items_schema = schema.get("items")
    if not isinstance(items_schema, dict):
        return
    for index, item in enumerate(payload):
        validate_structured_payload_lightweight(item, items_schema, f"{path}[{index}]")


def _schema_validation_error(detail: str) -> LLMStructuredOutputError:
    return LLMStructuredOutputError(f"LLM structured response did not match output schema: {detail}", "schema_mismatch")


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


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _format_expected_type(expected_type: Any) -> str:
    if isinstance(expected_type, list):
        return " or ".join(str(item) for item in expected_type)
    return str(expected_type)


def _format_jsonschema_error(exc: Any) -> str:
    path = "$"
    for part in exc.path:
        path = _join_schema_path(path, part)
    return f"{path}: {exc.message}"


def _join_schema_path(path: str, part: Any) -> str:
    if isinstance(part, int):
        return f"{path}[{part}]"
    key = str(part)
    if key.isidentifier():
        return f"{path}.{key}"
    return f"{path}[{key!r}]"
