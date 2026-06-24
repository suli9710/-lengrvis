from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any

from app.config import AppSettings
from app.llm.base import LLMProvider
from app.llm.profiles import ProviderProfile, profile_for_provider
from app.llm.types import LLMResponse
from app.llm.usage import estimate_usage, record_llm_response

from .compaction import (
    ContextProjection,
    _load_session_context,
    _record_event,
    force_compact_for_retry,
    project_messages_for_llm,
    provider_safe_projection_fallback,
)
from .errors import LLMCapabilityError, PromptTooLongError, is_prompt_too_long_error
from .messages import _normalize_messages
from .text_utils import _json
from .tokens import count_messages_tokens


def build_llm_request_snapshot(
    projection: ContextProjection,
    settings: AppSettings,
    *,
    task: str,
    purpose: str,
    provider: str,
    model: str,
    tools: list[dict[str, Any]] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_hash = hashlib.sha256(
        json.dumps(projection.messages, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    try:
        from app.policy.approval_binding import permission_policy_version
        from app.policy.permissions import PermissionStore

        policy_version = permission_policy_version(PermissionStore().updated_at())
    except Exception:
        policy_version = ""
    return {
        "snapshot_id": f"ctx_{prompt_hash[:16]}",
        "prompt_hash": prompt_hash,
        "visible_tool_ids": _visible_tool_ids(tools),
        "policy_version": policy_version,
        "context_projection": projection.to_dict(),
        "routing": {
            "mode": settings.mode,
            "permission_mode": getattr(settings, "permission_mode", "default"),
            "task": task,
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "profile": profile or {},
        },
    }


def _visible_tool_ids(tools: list[dict[str, Any]] | None) -> list[str]:
    result: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            result.append(str(function.get("name")))
        elif tool.get("name"):
            result.append(str(tool.get("name")))
    return sorted({item for item in result if item})


class ContextAwareProvider(LLMProvider):
    name = "context_aware"

    def __init__(
        self,
        provider: LLMProvider,
        settings: AppSettings,
        *,
        task: str = "default",
        profile: ProviderProfile | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.task = task
        self.name = getattr(provider, "name", self.name)
        self.profile = profile or profile_for_provider(provider, settings)

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
        if tools and not self.profile.capabilities.tools:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support tool calls.")
        projection = self.prepare(messages, purpose=f"{self.task}:chat")
        try:
            response = await self._provider_chat_result(
                projection.messages,  # type: ignore[arg-type]
                model=model,
                temperature=temperature,
                tools=tools,
            )
        except Exception as exc:
            if not isinstance(exc, PromptTooLongError) and not is_prompt_too_long_error(exc):
                raise
            retry_projection = force_compact_for_retry(projection.messages, self.settings)
            _record_event(
                "context.reactive_retry",
                "ContextManager",
                {
                    "task": self.task,
                    "original_tokens": retry_projection.original_tokens,
                    "projected_tokens": retry_projection.projected_tokens,
                },
            )
            try:
                response = await self._provider_chat_result(
                    retry_projection.messages,  # type: ignore[arg-type]
                    model=model,
                    temperature=temperature,
                    tools=tools,
                )
                projection = retry_projection
            except Exception as retry_exc:
                if not isinstance(retry_exc, PromptTooLongError) and not is_prompt_too_long_error(retry_exc):
                    raise
                fallback_projection = provider_safe_projection_fallback(
                    projection.messages,
                    self.settings,
                    source="reactive_retry_fallback",
                )
                _record_event(
                    "context.reactive_retry_fallback",
                    "ContextManager",
                    {
                        "task": self.task,
                        "original_tokens": fallback_projection.original_tokens,
                        "projected_tokens": fallback_projection.projected_tokens,
                        "projected_messages": fallback_projection.projected_count,
                    },
                )
                response = await self._provider_chat_result(
                    fallback_projection.messages,  # type: ignore[arg-type]
                    model=model,
                    temperature=temperature,
                    tools=tools,
                )
                projection = fallback_projection
        response = self._with_request_snapshot(response, projection, purpose="chat", tools=tools)
        response = self._with_cost(response)
        request_snapshot = response.metadata.get("request_snapshot") if isinstance(response.metadata, dict) else {}
        record_llm_response(
            response,
            self.settings,
            task=self.task,
            purpose="chat",
            profile=self.profile.to_dict(),
            projection={
                **projection.to_dict(),
                "context_usage": _safe_context_usage_snapshot(projection, self.settings),
                "request_snapshot": request_snapshot,
            },
        )
        return response

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        if not self.profile.capabilities.structured_json:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support structured JSON.")
        projection = self.prepare(messages, purpose=f"{self.task}:structured")
        try:
            payload = await self.provider.structured_chat(
                projection.messages,  # type: ignore[arg-type]
                output_schema,
            )
        except Exception as exc:
            if not isinstance(exc, PromptTooLongError) and not is_prompt_too_long_error(exc):
                raise
            retry_projection = force_compact_for_retry(projection.messages, self.settings)
            _record_event(
                "context.reactive_retry",
                "ContextManager",
                {
                    "task": self.task,
                    "structured": True,
                    "original_tokens": retry_projection.original_tokens,
                    "projected_tokens": retry_projection.projected_tokens,
                },
            )
            try:
                payload = await self.provider.structured_chat(
                    retry_projection.messages,  # type: ignore[arg-type]
                    output_schema,
                )
                projection = retry_projection
            except Exception as retry_exc:
                if not isinstance(retry_exc, PromptTooLongError) and not is_prompt_too_long_error(retry_exc):
                    raise
                fallback_projection = provider_safe_projection_fallback(
                    projection.messages,
                    self.settings,
                    source="reactive_retry_fallback",
                )
                _record_event(
                    "context.reactive_retry_fallback",
                    "ContextManager",
                    {
                        "task": self.task,
                        "structured": True,
                        "original_tokens": fallback_projection.original_tokens,
                        "projected_tokens": fallback_projection.projected_tokens,
                        "projected_messages": fallback_projection.projected_count,
                    },
                )
                payload = await self.provider.structured_chat(
                    fallback_projection.messages,  # type: ignore[arg-type]
                    output_schema,
                )
                projection = fallback_projection
        payload_json = _json(payload)
        structured_response = LLMResponse(
            content=payload_json,
            provider=getattr(self.provider, "name", self.profile.provider_name),
            model=self.profile.model,
            usage=estimate_usage(projection.messages, payload_json),
            metadata={"structured": True},
        )
        structured_response = self._with_request_snapshot(
            structured_response,
            projection,
            purpose="structured_chat",
            tools=None,
        )
        structured_response = self._with_cost(structured_response)
        request_snapshot = structured_response.metadata.get("request_snapshot") if isinstance(structured_response.metadata, dict) else {}
        record_llm_response(
            structured_response,
            self.settings,
            task=self.task,
            purpose="structured_chat",
            profile=self.profile.to_dict(),
            projection={
                **projection.to_dict(),
                "context_usage": _safe_context_usage_snapshot(projection, self.settings),
                "request_snapshot": request_snapshot,
            },
        )
        return payload

    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        if not self.profile.capabilities.embeddings:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support embeddings.")
        return await self.provider.embed(texts, model=model)

    async def rerank(self, query: str, documents: list[str]) -> list[int]:
        return await self.provider.rerank(query, documents)

    async def vision(self, image_path: str, prompt: str, model: str | None = None) -> str:
        if not self.profile.capabilities.vision:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support vision.")
        try:
            return await self.provider.vision(image_path, prompt, model=model)  # type: ignore[call-arg]
        except TypeError:
            return await self.provider.vision(image_path, prompt)

    async def ocr(self, image_path: str) -> str:
        return await self.provider.ocr(image_path)

    async def summarize(self, text: str) -> str:
        return await self.provider.summarize(text)

    def prepare(self, messages: list[dict[str, Any]], *, purpose: str) -> ContextProjection:
        if purpose.endswith(":compact") or purpose.endswith(":session_memory"):
            normalized = _normalize_messages(messages)
            token_count = count_messages_tokens(normalized)
            return ContextProjection(
                messages=normalized,
                original_count=len(normalized),
                projected_count=len(normalized),
                original_tokens=token_count,
                projected_tokens=token_count,
                source=purpose,
            )
        return project_messages_for_llm(
            messages,
            self.settings,
            session_context=_load_session_context(),
            source=purpose,
        )

    async def _provider_chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        chat_result = getattr(self.provider, "chat_result", None)
        if callable(chat_result):
            return await chat_result(messages, model=model, temperature=temperature, tools=tools)
        content = await self.provider.chat(messages, model=model, temperature=temperature, tools=tools)
        return LLMResponse(
            content=content,
            provider=getattr(self.provider, "name", self.profile.provider_name),
            model=model or self.profile.model,
            usage=estimate_usage(messages, content),
        )

    def _with_cost(self, response: LLMResponse) -> LLMResponse:
        if response.cost is not None:
            return response

        return replace(response, cost=self.profile.estimate_cost(response.usage))

    def _with_request_snapshot(
        self,
        response: LLMResponse,
        projection: ContextProjection,
        *,
        purpose: str,
        tools: list[dict[str, Any]] | None,
    ) -> LLMResponse:
        snapshot = build_llm_request_snapshot(
            projection,
            self.settings,
            task=self.task,
            purpose=purpose,
            provider=response.provider,
            model=response.model,
            tools=tools,
            profile=self.profile.to_dict(),
        )
        metadata = {**(response.metadata or {}), "request_snapshot": snapshot}
        return replace(response, metadata=metadata)


def _safe_context_usage_snapshot(projection: ContextProjection, settings: AppSettings) -> dict[str, Any]:
    try:
        from app.context_usage import analyze_context_usage, context_usage_to_dict

        return context_usage_to_dict(
            analyze_context_usage(
                messages=projection.messages,
                settings=settings,
                include_registered_tools=False,
                include_session_memory=False,
                include_projection=True,
            )
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
