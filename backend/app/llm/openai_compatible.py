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
        self._ensure_circuit_allows_request(circuit_key)
        attempts = max(0, self.settings.llm_api_max_retries) + 1
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                    response = await client.post(
                        endpoint,
                        headers=self._headers(),
                        json=payload,
                    )
                    response.raise_for_status()
                    try:
                        data = response.json()
                    except ValueError as exc:
                        content_type = response.headers.get("content-type", "")
                        raise LLMApiResponseError(
                            f"LLM provider returned non-JSON response with content-type {content_type or 'unknown'}."
                        ) from exc
                self._record_success(circuit_key)
                return data
            except Exception as exc:
                last_error = exc
                if is_prompt_too_long_error(exc):
                    raise prompt_too_long_error_from_exception(
                        exc,
                        provider=self.settings.provider_name,
                        model=model,
                    ) from exc
                if not self._should_retry(exc) or attempt == attempts - 1:
                    self._record_failure(circuit_key, exc)
                    raise
                await self._sleep_before_retry(attempt, last_error)

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
            metadata={"wire_api": "responses", "response_id": data.get("id")},
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
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                return json.loads(content[start : end + 1])
            raise

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
