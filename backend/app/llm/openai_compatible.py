from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import AppSettings
from app.context_management import PromptTooLongError, is_prompt_too_long_error, prompt_too_long_error_from_exception
from app.llm.base import LLMProvider
from app.llm.prompts import load_prompt, render_prompt
from app.llm.types import LLMResponse, LLMUsage
from app.llm.usage import estimate_usage


class LLMApiCircuitOpen(RuntimeError):
    """Raised when repeated transient failures temporarily block provider calls."""


class LLMApiResponseError(RuntimeError):
    """Raised when a provider returns a syntactically successful but invalid body."""


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


_CIRCUITS: dict[tuple[str, str, str, str], _CircuitState] = {}


def normalize_openai_base_url(base_url: str) -> str:
    """Treat a bare OpenAI-compatible origin as an API base rooted at /v1."""
    raw = str(base_url or "").strip().rstrip("/")
    if not raw:
        return raw
    split = urlsplit(raw)
    path = split.path.rstrip("/")
    if split.scheme and split.netloc and path in {"", "/"}:
        return urlunsplit((split.scheme, split.netloc, "/v1", split.query, split.fragment)).rstrip("/")
    return raw


def circuit_snapshot(settings: AppSettings) -> dict[str, Any]:
    endpoint_kind = "responses" if (settings.wire_api or "").lower() == "responses" else "chat"
    key = (
        settings.provider_name.lower(),
        normalize_openai_base_url(settings.base_url),
        endpoint_kind,
        settings.model,
    )
    state = _CIRCUITS.get(key)
    if state is None:
        return {"state": "closed", "failures": 0, "retry_after_seconds": 0.0}
    retry_after = 0.0
    if state.opened_at is not None:
        retry_after = max(0.0, settings.llm_api_circuit_cooldown_seconds - (time.monotonic() - state.opened_at))
    return {
        "state": "open" if state.opened_at is not None and retry_after > 0 else "closed",
        "failures": state.failures,
        "retry_after_seconds": round(retry_after, 3),
    }


class OpenAICompatibleProvider(LLMProvider):
    name = "openai_compatible"

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._last_transport_metadata: dict[str, Any] = {}

    def transport_metadata(self) -> dict[str, Any]:
        return dict(self._last_transport_metadata)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.requires_openai_auth and self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        return headers

    def _api_base_url(self) -> str:
        return normalize_openai_base_url(self.settings.base_url)

    def _chat_endpoint(self) -> str:
        base_url = self._api_base_url()
        if self.settings.wire_api.lower() == "responses":
            return f"{base_url}/responses"
        return f"{base_url}/chat/completions"

    def _circuit_key(self, endpoint_kind: str, model: str) -> tuple[str, str, str, str]:
        return (
            self.settings.provider_name.lower(),
            self._api_base_url(),
            endpoint_kind,
            model,
        )

    async def _post_json(self, endpoint: str, payload: dict[str, Any], *, endpoint_kind: str, model: str) -> dict[str, Any]:
        circuit_key = self._circuit_key(endpoint_kind, model)
        attempts = max(0, self.settings.llm_api_max_retries) + 1
        last_error: Exception | None = None
        trace: dict[str, Any] = {
            "endpoint_kind": endpoint_kind,
            "endpoint": endpoint,
            "model": model,
            "attempts": 0,
            "max_attempts": attempts,
            "retry_events": [],
            "circuit_before": circuit_snapshot(self.settings),
            "ok": False,
        }
        self._last_transport_metadata = trace
        try:
            self._ensure_circuit_allows_request(circuit_key)
        except Exception as exc:
            trace["error_type"] = exc.__class__.__name__
            trace["error"] = str(exc)
            trace["circuit_open"] = isinstance(exc, LLMApiCircuitOpen)
            self._last_transport_metadata = trace
            raise

        for attempt in range(attempts):
            try:
                trace["attempts"] = attempt + 1
                async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                    response = await client.post(
                        endpoint,
                        headers=self._headers(),
                        json=payload,
                    )
                    trace["last_status_code"] = response.status_code
                    response.raise_for_status()
                    try:
                        data = response.json()
                    except ValueError as exc:
                        content_type = response.headers.get("content-type", "")
                        raise LLMApiResponseError(
                            f"LLM provider returned non-JSON response with content-type {content_type or 'unknown'}."
                        ) from exc
                self._record_success(circuit_key)
                trace["ok"] = True
                trace["circuit_after"] = circuit_snapshot(self.settings)
                self._last_transport_metadata = trace
                return data
            except Exception as exc:
                last_error = exc
                if is_prompt_too_long_error(exc):
                    prompt_error = prompt_too_long_error_from_exception(
                        exc,
                        provider=self.settings.provider_name,
                        model=model,
                    )
                    trace["error_type"] = prompt_error.__class__.__name__
                    trace["prompt_too_long"] = prompt_error.to_dict()
                    trace["circuit_after"] = circuit_snapshot(self.settings)
                    self._last_transport_metadata = trace
                    raise prompt_error from exc
                if not self._should_retry(exc) or attempt == attempts - 1:
                    self._record_failure(circuit_key, exc)
                    trace["error_type"] = exc.__class__.__name__
                    trace["error"] = str(exc)
                    trace["circuit_after"] = circuit_snapshot(self.settings)
                    self._last_transport_metadata = trace
                    raise
                trace["retry_events"].append(
                    {
                        "attempt": attempt + 1,
                        "error_type": exc.__class__.__name__,
                        "status_code": _status_code(exc),
                        "retry": True,
                    }
                )
                self._last_transport_metadata = trace
                await self._sleep_before_retry(attempt, last_error)

        trace["error"] = str(last_error or RuntimeError("LLM API request failed."))
        self._last_transport_metadata = trace
        raise last_error or RuntimeError("LLM API request failed.")

    def _ensure_circuit_allows_request(self, circuit_key: tuple[str, str, str, str]) -> None:
        state = _CIRCUITS.get(circuit_key)
        if state is None or state.opened_at is None:
            return
        cooldown = self.settings.llm_api_circuit_cooldown_seconds
        elapsed = time.monotonic() - state.opened_at
        if elapsed < cooldown:
            raise LLMApiCircuitOpen(
                f"LLM API circuit is open for {self.settings.provider_name}; retry after {cooldown - elapsed:.1f}s."
            )
        state.failures = 0
        state.opened_at = None

    def _record_success(self, circuit_key: tuple[str, str, str, str]) -> None:
        _CIRCUITS.pop(circuit_key, None)

    def _record_failure(self, circuit_key: tuple[str, str, str, str], exc: Exception) -> None:
        if not self._should_count_for_circuit(exc):
            return
        state = _CIRCUITS.setdefault(circuit_key, _CircuitState())
        state.failures += 1
        if state.failures >= max(1, self.settings.llm_api_circuit_failure_threshold):
            state.opened_at = time.monotonic()

    def _should_retry(self, exc: Exception) -> bool:
        if isinstance(exc, LLMApiCircuitOpen):
            return False
        if isinstance(exc, PromptTooLongError) or is_prompt_too_long_error(exc):
            return False
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            return status_code == 429 or 500 <= status_code < 600
        return False

    def _should_count_for_circuit(self, exc: Exception) -> bool:
        if isinstance(exc, LLMApiCircuitOpen):
            return False
        if isinstance(exc, PromptTooLongError) or is_prompt_too_long_error(exc):
            return False
        return self._should_retry(exc)

    async def _sleep_before_retry(self, attempt: int, exc: Exception | None = None) -> None:
        delay = self._retry_after_seconds(exc)
        if delay is None:
            base_delay = self.settings.llm_api_retry_backoff_seconds * (2**attempt)
            jitter = random.uniform(0, base_delay * 0.1) if base_delay > 0 else 0
            delay = base_delay + jitter
        if delay <= 0:
            return
        import asyncio

        await asyncio.sleep(delay)

    def _retry_after_seconds(self, exc: Exception | None) -> float | None:
        if not isinstance(exc, httpx.HTTPStatusError):
            return None
        raw = exc.response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return max(0.0, parsed.timestamp() - time.time())

    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        return (await self.chat_result(messages, model=model, temperature=temperature, tools=tools)).content

    async def chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        if self.settings.wire_api.lower() == "responses":
            return await self._responses_chat_result(messages, model=model, temperature=temperature, tools=tools)

        target_model = model or self.settings.model
        wire_messages = [_chat_message_payload(message) for message in messages]
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": wire_messages,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_tokens": self.settings.max_tokens,
        }
        if tools:
            payload["tools"] = tools
        data = await self._post_json(self._chat_endpoint(), payload, endpoint_kind="chat", model=target_model)
        self._raise_for_embedded_error(data)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMApiResponseError("LLM chat response did not include any choices.")
        choice = (data.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            raise LLMApiResponseError("LLM chat response choice was malformed.")
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            raise LLMApiResponseError("LLM chat response message was malformed.")
        content = message.get("content") or ""
        if content == "" and not message.get("tool_calls"):
            raise LLMApiResponseError("LLM chat response did not include content.")
        return LLMResponse(
            content=content,
            provider=self.name,
            model=target_model,
            usage=self._usage_from_chat_completions(data, wire_messages, content),
            finish_reason=str(choice.get("finish_reason") or ""),
            metadata={
                "wire_api": "chat_completions",
                "tool_calls": message.get("tool_calls") or [],
                "api_trace": self.transport_metadata(),
            },
        )

    async def _responses_chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        return (await self._responses_chat_result(messages, model=model, temperature=temperature, tools=tools)).content

    async def _responses_chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        if any(message.get("role") == "tool" for message in messages):
            raise NotImplementedError("Responses API transport does not yet map tool-role messages safely.")
        input_items = [
            {"role": message["role"], "content": message.get("content", "")}
            for message in messages
            if message.get("role") in {"developer", "system", "user", "assistant"}
        ]
        target_model = model or self.settings.model
        payload: dict[str, Any] = {
            "model": target_model,
            "input": input_items,
            "temperature": self.settings.temperature if temperature is None else temperature,
            "max_output_tokens": self.settings.max_tokens,
            "store": not self.settings.disable_response_storage,
        }
        if self.settings.model_reasoning_effort:
            payload["reasoning"] = {"effort": self.settings.model_reasoning_effort}
        if tools:
            payload["tools"] = tools
        data = await self._post_json(self._chat_endpoint(), payload, endpoint_kind="responses", model=target_model)
        self._raise_for_embedded_error(data)
        status = str(data.get("status") or "")
        if status in {"failed", "cancelled", "incomplete"}:
            detail = data.get("incomplete_details") or data.get("error") or status
            raise LLMApiResponseError(f"LLM responses API returned terminal status: {detail}")
        content = self._extract_responses_text(data)
        if not content:
            raise LLMApiResponseError("LLM responses API did not include output text.")
        return LLMResponse(
            content=content,
            provider=self.name,
            model=target_model,
            usage=self._usage_from_responses(data, messages, content),
            finish_reason=str(data.get("status") or ""),
            metadata={"wire_api": "responses", "response_id": data.get("id"), "api_trace": self.transport_metadata()},
        )

    def _extract_responses_text(self, data: dict[str, Any]) -> str:
        if isinstance(data.get("output_text"), str):
            return data["output_text"]

        parts: list[str] = []
        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                if isinstance(content.get("text"), str):
                    parts.append(content["text"])
        return "".join(parts)

    def _raise_for_embedded_error(self, data: dict[str, Any]) -> None:
        error = data.get("error")
        if error:
            if isinstance(error, dict):
                message = error.get("message") or error.get("type") or "provider error"
            else:
                message = str(error)
            raise LLMApiResponseError(f"LLM provider returned an error payload: {message}")

    def _usage_from_chat_completions(
        self,
        data: dict[str, Any],
        messages: list[dict[str, Any]],
        content: str,
    ) -> LLMUsage:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return estimate_usage(messages, content)
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        return LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated=False,
            details={key: value for key, value in usage.items() if str(key).endswith("_details")},
        )

    def _usage_from_responses(
        self,
        data: dict[str, Any],
        messages: list[dict[str, Any]],
        content: str,
    ) -> LLMUsage:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return estimate_usage(messages, content)
        prompt_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
        return LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated=False,
            details={key: value for key, value in usage.items() if str(key).endswith("_details")},
        )

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        schema_prompt = {
            "role": "system",
            "content": render_prompt("structured_json_schema.md", {"schema": json.dumps(output_schema)}),
        }
        content = await self.chat([schema_prompt, *messages], temperature=0)
        payload = _parse_structured_json(content)
        _validate_structured_payload(payload, output_schema)
        return payload

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        target_model = model or self.settings.embedding_model
        payload = {"model": target_model, "input": texts}
        data = await self._post_json(
            f"{self._api_base_url()}/embeddings",
            payload,
            endpoint_kind="embeddings",
            model=target_model,
        )
        return [item["embedding"] for item in data["data"]]

    async def vision(self, image_path: str, prompt: str, model: str | None = None) -> str:
        import base64
        from pathlib import Path

        path = Path(image_path)
        if not path.exists():
            return f"[vision] file not found: {image_path}"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        suffix = path.suffix.lstrip(".").lower() or "png"
        mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
        data_url = f"data:{mime};base64,{encoded}"
        target_model = model or self.settings.vision_model or self.settings.model
        if self.settings.wire_api.lower() == "responses":
            payload: dict[str, Any] = {
                "model": target_model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": data_url},
                        ],
                    }
                ],
                "temperature": self.settings.temperature,
                "max_output_tokens": self.settings.max_tokens,
                "store": not self.settings.disable_response_storage,
            }
            data = await self._post_json(
                self._chat_endpoint(),
                payload,
                endpoint_kind="responses_vision",
                model=target_model,
            )
            self._raise_for_embedded_error(data)
            status = str(data.get("status") or "")
            if status in {"failed", "cancelled", "incomplete"}:
                detail = data.get("incomplete_details") or data.get("error") or status
                raise LLMApiResponseError(f"LLM responses API returned terminal status: {detail}")
            content = self._extract_responses_text(data)
            if not content:
                raise LLMApiResponseError("LLM responses API did not include output text.")
            return content
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
        }
        data = await self._post_json(
            f"{self._api_base_url()}/chat/completions",
            payload,
            endpoint_kind="vision",
            model=target_model,
        )
        self._raise_for_embedded_error(data)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMApiResponseError("LLM vision response did not include any choices.")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMApiResponseError("LLM vision response choice was malformed.")
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            raise LLMApiResponseError("LLM vision response message was malformed.")
        content = message.get("content") or ""
        if not content:
            raise LLMApiResponseError("LLM vision response did not include content.")
        return content

    async def ocr(self, image_path: str) -> str:
        return await self.vision(image_path, load_prompt("vision_ocr.md"))


def _parse_structured_json(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as original:
        decoder = json.JSONDecoder()
        for index, char in enumerate(content):
            if char not in "{[":
                continue
            try:
                payload, _ = decoder.raw_decode(content[index:])
            except json.JSONDecodeError:
                continue
            return payload
        raise LLMApiResponseError("LLM structured response was not valid JSON.") from original


def _validate_structured_payload(payload: Any, output_schema: dict[str, Any]) -> None:
    if not isinstance(output_schema, dict):
        raise LLMApiResponseError("LLM output schema must be a JSON Schema object.")
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError
    except Exception:
        _validate_structured_payload_lightweight(payload, output_schema)
        return

    try:
        Draft202012Validator.check_schema(output_schema)
        Draft202012Validator(output_schema).validate(payload)
    except SchemaError as exc:
        raise LLMApiResponseError(f"LLM output schema is invalid: {exc.message}") from exc
    except ValidationError as exc:
        raise LLMApiResponseError(
            f"LLM structured response did not match output schema: {_format_jsonschema_error(exc)}"
        ) from exc


def _validate_structured_payload_lightweight(payload: Any, schema: dict[str, Any], path: str = "$") -> None:
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
    return schema_type == "object" or isinstance(payload, dict) and any(
        key in schema for key in ("required", "properties", "additionalProperties")
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
            _validate_structured_payload_lightweight(payload[key], property_schema, _join_schema_path(path, key))

    additional_properties = schema.get("additionalProperties", True)
    extra_keys = [key for key in payload if key not in properties]
    if additional_properties is False and extra_keys:
        extra = ", ".join(str(key) for key in extra_keys)
        raise _schema_validation_error(f"{path} included unexpected field(s): {extra}.")
    if isinstance(additional_properties, dict):
        for key in extra_keys:
            _validate_structured_payload_lightweight(
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
        _validate_structured_payload_lightweight(item, items_schema, f"{path}[{index}]")


def _schema_validation_error(detail: str) -> LLMApiResponseError:
    return LLMApiResponseError(f"LLM structured response did not match output schema: {detail}")


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
        return isinstance(value, (int, float)) and not isinstance(value, bool)
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


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return int(status_code) if isinstance(status_code, int) else None


def _chat_message_payload(message: dict[str, Any]) -> dict[str, Any]:
    role = str(message.get("role") or "user")
    payload: dict[str, Any] = {
        "role": role,
        "content": message.get("content", ""),
    }
    if role != "tool" and message.get("name"):
        payload["name"] = message.get("name")
    if message.get("tool_calls"):
        payload["tool_calls"] = message.get("tool_calls")
    if role == "tool" and message.get("tool_call_id"):
        payload["tool_call_id"] = message.get("tool_call_id")
    return payload
