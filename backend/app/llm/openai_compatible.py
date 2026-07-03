from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.config import AppSettings
from app.context.management import PromptTooLongError, is_prompt_too_long_error, prompt_too_long_error_from_exception
from app.core.outbound_url import is_local_base_url, pin_outbound_http_url, validate_outbound_http_url
from app.llm.base import LLMProvider
from app.llm.prompts import load_prompt, render_prompt
from app.llm.structured_output import (
    LLMApiResponseError,
    LLMStructuredOutputError,
    safe_structured_excerpt,
)
from app.llm.structured_output import (
    check_output_schema as _check_structured_output_schema,
)
from app.llm.structured_output import (
    parse_and_validate_structured_content as _parse_and_validate_structured_content,
)
from app.llm.types import LLMResponse, LLMUsage
from app.llm.usage import estimate_usage


class LLMApiCircuitOpen(RuntimeError):
    """Raised when repeated transient failures temporarily block provider calls."""


# P1-10 fix: Redact API keys from error messages before surfacing to callers.
_API_KEY_PATTERN = re.compile(r"(sk-[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,})", re.IGNORECASE)


def _sanitize_error_message(message: str) -> str:
    """Remove API keys or bearer tokens from error messages to prevent credential leakage."""
    return _API_KEY_PATTERN.sub("[REDACTED]", str(message))


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


_CIRCUITS: dict[tuple[str, str, str, str], _CircuitState] = {}
_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP_LIMITS = httpx.Limits(max_keepalive_connections=20, max_connections=100, keepalive_expiry=30.0)


def _shared_http_client() -> httpx.AsyncClient:
    """Process-wide AsyncClient so LLM calls reuse TCP/TLS connections."""
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
        _SHARED_HTTP_CLIENT = httpx.AsyncClient(
            limits=_HTTP_LIMITS,
            timeout=httpx.Timeout(60.0),
            follow_redirects=False,
        )
    return _SHARED_HTTP_CLIENT


async def close_shared_http_client() -> None:
    global _SHARED_HTTP_CLIENT
    if _SHARED_HTTP_CLIENT is not None and not _SHARED_HTTP_CLIENT.is_closed:
        await _SHARED_HTTP_CLIENT.aclose()
    _SHARED_HTTP_CLIENT = None


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
        base = normalize_openai_base_url(self.settings.base_url)
        if base:
            validate_outbound_http_url(base, allow_private=self._allow_private_base(base))
        return base

    def _allow_private_base(self, base: str) -> bool:
        from app.llm.registry import LOCAL_PROVIDERS

        return is_local_base_url(base) and self.settings.provider_name.lower() in LOCAL_PROVIDERS

    def _chat_endpoint(self) -> str:
        base_url = self._api_base_url()
        if self.settings.wire_api.lower() == "responses":
            return f"{base_url}/responses"
        return self._chat_completions_endpoint()

    def _chat_completions_endpoint(self) -> str:
        base_url = self._api_base_url()
        return f"{base_url}/chat/completions"

    def _circuit_key(self, endpoint_kind: str, model: str) -> tuple[str, str, str, str]:
        return (
            self.settings.provider_name.lower(),
            self._api_base_url(),
            endpoint_kind,
            model,
        )

    async def _post_json(
        self, endpoint: str, payload: dict[str, Any], *, endpoint_kind: str, model: str
    ) -> dict[str, Any]:
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
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            trace["error_type"] = exc.__class__.__name__
            trace["error"] = _sanitize_error_message(str(exc))
            trace["circuit_open"] = isinstance(exc, LLMApiCircuitOpen)
            self._last_transport_metadata = trace
            raise

        for attempt in range(attempts):
            try:
                trace["attempts"] = attempt + 1
                client = _shared_http_client()
                # Re-pin per attempt (DNS-rebinding TOCTOU): connect to the IP
                # that passed SSRF validation; Host/SNI keep the real hostname
                # so TLS certificate verification is unchanged.
                pinned = pin_outbound_http_url(endpoint, allow_private=self._allow_private_base(endpoint))
                response = await client.post(
                    pinned.url,
                    headers={**self._headers(), **pinned.headers},
                    json=payload,
                    timeout=self.settings.timeout,
                    extensions=dict(pinned.extensions),
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
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
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
                    # P1-10 fix: Sanitize error message to prevent API key leakage.
                    trace["error"] = _sanitize_error_message(str(exc))
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

        trace["error"] = _sanitize_error_message(str(last_error or RuntimeError("LLM API request failed.")))
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
        if isinstance(exc, httpx.TimeoutException | httpx.NetworkError | httpx.RemoteProtocolError):
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
            jitter = random.uniform(0, base_delay * 0.1) if base_delay > 0 else 0  # noqa: S311
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
                parsed = parsed.replace(tzinfo=UTC)
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

        return await self._chat_completions_chat_result(
            messages,
            model=model,
            temperature=temperature,
            tools=tools,
        )

    async def _chat_completions_chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> LLMResponse:
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
        data = await self._post_json(
            self._chat_completions_endpoint(), payload, endpoint_kind="chat", model=target_model
        )
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
                **(metadata or {}),
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
        if _requires_chat_completions_tool_mapping(messages):
            return await self._chat_completions_chat_result(
                messages,
                model=model,
                temperature=temperature,
                tools=tools,
                metadata={
                    "wire_api_requested": "responses",
                    "wire_api_fallback_reason": "tool_message_mapping_requires_chat_completions",
                },
            )
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
            # P1-10 fix: Sanitize embedded error messages to prevent API key leakage.
            raise LLMApiResponseError(_sanitize_error_message(f"LLM provider returned an error payload: {message}"))

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

    async def _structured_chat_prompt(
        self,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        schema_prompt = {
            "role": "system",
            "content": render_prompt("structured_json_schema.md", {"schema": json.dumps(output_schema)}),
        }
        content = await self.chat([schema_prompt, *messages], temperature=0)
        return await self._parse_structured_content_with_repair(content, output_schema)

    async def _structured_chat_native(
        self,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
    ) -> dict[str, Any]:
        if self.settings.wire_api.lower() == "responses":
            content = await self._structured_responses_content(messages, output_schema)
        else:
            content = await self._structured_chat_completions_content(messages, output_schema)
        return await self._parse_structured_content_with_repair(content, output_schema)

    async def _structured_chat_completions_content(
        self, messages: list[dict[str, str]], output_schema: dict[str, Any]
    ) -> str:
        target_model = self.settings.model
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": [_chat_message_payload(message) for message in messages],
            "temperature": 0,
            "max_tokens": self.settings.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": _native_json_schema_spec(output_schema),
            },
        }
        data = await self._post_json(
            self._chat_completions_endpoint(), payload, endpoint_kind="structured_chat", model=target_model
        )
        self._raise_for_embedded_error(data)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMStructuredOutputError(
                "LLM structured response choice was malformed.",
                "malformed_provider_response",
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise LLMStructuredOutputError(
                "LLM structured response choice was malformed.",
                "malformed_provider_response",
            )
        message = choice.get("message") or {}
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise LLMStructuredOutputError(
                "LLM structured response did not include output text.",
                "malformed_provider_response",
            )
        return message["content"]

    async def _structured_responses_content(
        self,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
    ) -> str:
        input_items = [
            {"role": message["role"], "content": message.get("content", "")}
            for message in messages
            if message.get("role") in {"developer", "system", "user", "assistant"}
        ]
        target_model = self.settings.model
        payload: dict[str, Any] = {
            "model": target_model,
            "input": input_items,
            "temperature": 0,
            "max_output_tokens": self.settings.max_tokens,
            "store": not self.settings.disable_response_storage,
            "text": {"format": _native_json_schema_format(output_schema)},
        }
        if self.settings.model_reasoning_effort:
            payload["reasoning"] = {"effort": self.settings.model_reasoning_effort}
        data = await self._post_json(
            self._chat_endpoint(),
            payload,
            endpoint_kind="structured_responses",
            model=target_model,
        )
        self._raise_for_embedded_error(data)
        status = str(data.get("status") or "")
        if status in {"failed", "cancelled", "incomplete"}:
            raise LLMStructuredOutputError(
                "LLM responses API returned a terminal structured-output status.",
                "malformed_provider_response",
            )
        content = self._extract_responses_text(data)
        if not content:
            raise LLMStructuredOutputError(
                "LLM responses API did not include structured output text.",
                "malformed_provider_response",
            )
        return content

    async def _parse_structured_content_with_repair(
        self, content: str, output_schema: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            return _parse_and_validate_structured_content(content, output_schema)
        except LLMStructuredOutputError as exc:
            last_error = exc

        retries = max(0, int(getattr(self.settings, "structured_output_repair_retries", 1) or 0))
        if retries == 0:
            raise last_error
        repair_content = content
        for _attempt in range(retries):
            repair_prompt = {
                "role": "system",
                "content": render_prompt(
                    "structured_json_repair.md",
                    {
                        "schema": json.dumps(output_schema, ensure_ascii=False),
                        "failure_kind": last_error.failure_kind,
                        "output_excerpt": safe_structured_excerpt(repair_content),
                    },
                ),
            }
            repair_content = await self.chat([repair_prompt], temperature=0)
            try:
                return _parse_and_validate_structured_content(repair_content, output_schema)
            except LLMStructuredOutputError as exc:
                last_error = exc

        raise LLMStructuredOutputError(
            f"LLM structured response could not be repaired ({last_error.failure_kind}).",
            last_error.failure_kind,
        ) from last_error

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        _check_structured_output_schema(output_schema)
        mode = _structured_output_mode(self.settings)
        if mode in {"auto", "native"}:
            try:
                return await self._structured_chat_native(messages, output_schema)
            except httpx.HTTPStatusError as exc:
                if not _is_native_schema_unsupported(exc):
                    raise
                if mode == "native":
                    raise LLMStructuredOutputError(
                        "LLM provider does not support native structured JSON.",
                        "native_unsupported",
                    ) from exc
            except LLMStructuredOutputError:
                raise

        return await self._structured_chat_prompt(messages, output_schema)

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

    # P0-8 fix: Whitelisted image extensions and path traversal validation for vision().
    _ALLOWED_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff"})
    _MAX_IMAGE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB

    def _prepare_vision_image_path(
        self,
        image_path: str,
        *,
        allowed_directories: list[str] | None = None,
    ) -> tuple[Path | None, str | None]:
        import os

        from app.core.errors import SecurityError
        from app.core.paths import is_sensitive_path, is_system_path, normalize_path, resolve_authorized

        raw = Path(image_path)
        if ".." in raw.parts:
            return None, "[vision] invalid image path: directory traversal not allowed"

        try:
            if allowed_directories is not None:
                path = resolve_authorized(image_path, [str(item) for item in allowed_directories])
            else:
                if not raw.exists():
                    return None, f"[vision] file not found: {image_path}"
                if raw.is_symlink() or os.path.islink(raw):
                    return None, "[vision] invalid image path: symbolic links require authorized directories"
                path = normalize_path(image_path)
                if is_system_path(path) or is_sensitive_path(path):
                    return None, "[vision] invalid image path: sensitive or system paths are not allowed"
        except SecurityError as exc:
            return None, f"[vision] invalid image path: {exc}"
        except OSError:
            return None, f"[vision] file not found: {image_path}"

        if not path.is_file():
            return None, f"[vision] file not found: {image_path}"

        # P0-3 fix: validate the resolved real path, not the caller-supplied name.
        resolved = path.resolve(strict=True)
        if is_system_path(resolved) or is_sensitive_path(resolved):
            return None, "[vision] invalid image path: sensitive or system paths are not allowed"

        suffix = resolved.suffix.lstrip(".").lower()
        if suffix not in self._ALLOWED_IMAGE_EXTENSIONS:
            return None, f"[vision] unsupported image format: .{suffix or 'unknown'}"

        try:
            file_size = resolved.stat().st_size
        except OSError:
            return None, f"[vision] cannot stat file: {image_path}"
        if file_size > self._MAX_IMAGE_SIZE_BYTES:
            return None, (f"[vision] image file too large ({file_size} bytes; max {self._MAX_IMAGE_SIZE_BYTES})")
        if file_size == 0:
            return None, "[vision] image file is empty"
        return resolved, None

    async def vision(
        self,
        image_path: str,
        prompt: str,
        model: str | None = None,
        *,
        allowed_directories: list[str] | None = None,
    ) -> str:
        import base64

        path, error = self._prepare_vision_image_path(image_path, allowed_directories=allowed_directories)
        if error or path is None:
            return error or f"[vision] file not found: {image_path}"

        suffix = path.suffix.lstrip(".").lower()
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
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


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return int(status_code) if isinstance(status_code, int) else None


def _structured_output_mode(settings: AppSettings) -> str:
    mode = str(getattr(settings, "structured_output_mode", "auto") or "auto").strip().lower()
    return mode if mode in {"auto", "native", "prompt"} else "auto"


def _native_json_schema_format(output_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        **_native_json_schema_spec(output_schema),
    }


def _native_json_schema_spec(output_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": "lengrvis_structured_response",
        "schema": output_schema,
        "strict": True,
    }


def _is_native_schema_unsupported(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    status_code = exc.response.status_code
    if status_code not in {400, 422}:
        return False
    text = _http_error_text(exc).lower()
    schema_terms = ("response_format", "json_schema", "json schema", "text.format")
    unsupported_terms = (
        "unsupported",
        "not support",
        "unknown parameter",
        "unrecognized",
        "invalid parameter",
        "extra inputs are not permitted",
    )
    return any(term in text for term in schema_terms) and any(term in text for term in unsupported_terms)


def _http_error_text(exc: httpx.HTTPStatusError) -> str:
    try:
        payload = exc.response.json()
    except ValueError:
        return exc.response.text
    return json.dumps(payload, ensure_ascii=False)


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


def _requires_chat_completions_tool_mapping(messages: list[dict[str, Any]]) -> bool:
    return any(message.get("role") == "tool" or bool(message.get("tool_calls")) for message in messages)
