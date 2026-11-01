from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from app.commerce.usage import reserve_cloud_quota_call
from app.config import AppSettings
from app.context import compaction_observability as compaction_metrics
from app.context.compact_boundaries import (
    compact_metadata as _compact_metadata,
)
from app.context.compact_boundaries import (
    latest_compact_boundary as _latest_compact_boundary,
)
from app.context.compact_boundaries import (
    retained_tail_message_ids as _retained_tail_message_ids,
)
from app.context.compaction_helpers import (
    _system_context_message,
    compact_boundary_view,
    repair_tool_message_invariants,
    select_recent_complete_tail,
)
from app.context.compaction_strategies import (
    auto_compact_messages,
    snip_history_if_needed,
)
from app.context.fallback_trim import (
    fallback_target_tokens as _fallback_target_tokens_from_values,
)
from app.context.fallback_trim import (
    trim_oldest_unprotected_blocks as _trim_oldest_unprotected_blocks,
)
from app.context.message_text import json_text as _json
from app.context.projection import (
    ContextProjection,
    _normalize_messages,
    _record_event,
    build_llm_request_snapshot,
    project_messages_for_llm,
)
from app.context.prompt_errors import (
    PromptTooLongError,
    is_prompt_too_long_error,
)
from app.context.session import _load_session_context
from app.context.tokens import (
    count_messages_tokens,
    effective_context_window,
    rough_token_count,
)
from app.llm.base import LLMProvider
from app.llm.profiles import ProviderProfile, profile_for_provider
from app.llm.prompts import load_prompt
from app.llm.types import LLMResponse, LLMUsage
from app.llm.usage import estimate_usage, record_llm_response
from app.observability.best_effort import log_best_effort_failure

logger = logging.getLogger(__name__)


class LLMCapabilityError(RuntimeError):
    """Raised when the active model profile cannot satisfy a requested capability."""


class ContextAwareProvider(LLMProvider):
    name = "context_aware"

    def __init__(
        self,
        provider: LLMProvider,
        settings: AppSettings,
        *,
        task: str = "default",
        profile: ProviderProfile | None = None,
        meter_cloud_usage: bool = False,
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.task = task
        self.name = getattr(provider, "name", self.name)
        self.profile = profile or profile_for_provider(provider, settings)
        self.meter_cloud_usage = bool(meter_cloud_usage)
        self.transport_metered = False
        configure_transport_meter = getattr(provider, "configure_cloud_usage_meter", None)
        if self.meter_cloud_usage and callable(configure_transport_meter):
            self.transport_metered = bool(
                configure_transport_meter(
                    task=self.task,
                    profile=self.profile,
                )
            )

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
        reservation_id: str | None = None
        try:
            response, reservation_id = await self._metered_provider_chat_result(
                projection.messages,  # type: ignore[arg-type]
                model=model,
                temperature=temperature,
                tools=tools,
            )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
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
                response, reservation_id = await self._metered_provider_chat_result(
                    retry_projection.messages,  # type: ignore[arg-type]
                    model=model,
                    temperature=temperature,
                    tools=tools,
                )
                projection = retry_projection
            except Exception as retry_exc:  # noqa: BLE001 - broad-exception-boundary
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
                response, reservation_id = await self._metered_provider_chat_result(
                    fallback_projection.messages,  # type: ignore[arg-type]
                    model=model,
                    temperature=temperature,
                    tools=tools,
                )
                projection = fallback_projection
        response = self._with_request_snapshot(response, projection, purpose="chat", tools=tools)
        response = self._with_cost(response)
        request_snapshot = response.metadata.get("request_snapshot") if isinstance(response.metadata, dict) else {}
        if not self.transport_metered:
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
                reservation_id=reservation_id,
                metered_cloud=self.meter_cloud_usage,
            )
        return response

    async def structured_chat(self, messages: list[dict[str, str]], output_schema: dict[str, Any]) -> dict[str, Any]:
        if not self.profile.capabilities.structured_json:
            raise LLMCapabilityError(f"Provider '{self.profile.provider_name}' does not support structured JSON.")
        projection = self.prepare(messages, purpose=f"{self.task}:structured")
        reservation_id: str | None = None
        try:
            payload, reservation_id = await self._metered_structured_chat(
                projection.messages,  # type: ignore[arg-type]
                output_schema,
            )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
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
                payload, reservation_id = await self._metered_structured_chat(
                    retry_projection.messages,  # type: ignore[arg-type]
                    output_schema,
                )
                projection = retry_projection
            except Exception as retry_exc:  # noqa: BLE001 - broad-exception-boundary
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
                payload, reservation_id = await self._metered_structured_chat(
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
        request_snapshot = (
            structured_response.metadata.get("request_snapshot")
            if isinstance(structured_response.metadata, dict)
            else {}
        )
        if not self.transport_metered:
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
                reservation_id=reservation_id,
                metered_cloud=self.meter_cloud_usage,
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

    async def _metered_provider_chat_result(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[LLMResponse, str | None]:
        reservation_id = self._reserve_cloud_call(
            messages,
            purpose="chat",
            model=model,
            extra_payload=tools,
        )
        response = await self._provider_chat_result(
            messages,
            model=model,
            temperature=temperature,
            tools=tools,
        )
        return response, reservation_id

    async def _metered_structured_chat(
        self,
        messages: list[dict[str, str]],
        output_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], str | None]:
        reservation_id = self._reserve_cloud_call(
            messages,
            purpose="structured_chat",
            extra_payload=output_schema,
        )
        payload = await self.provider.structured_chat(messages, output_schema)
        return payload, reservation_id

    def _reserve_cloud_call(
        self,
        messages: list[dict[str, Any]],
        *,
        purpose: str,
        model: str | None = None,
        extra_payload: Any = None,
    ) -> str | None:
        if not self.meter_cloud_usage or self.transport_metered:
            return None
        prompt_tokens = count_messages_tokens(messages)
        if extra_payload:
            prompt_tokens += rough_token_count(_json(extra_payload))
        max_completion_tokens = max(1, int(self.profile.model_profile.max_output_tokens))
        reserved_usage = LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=max_completion_tokens,
            total_tokens=prompt_tokens + max_completion_tokens,
            estimated=True,
        )
        max_cost = self.profile.estimate_cost(reserved_usage).total_cost_usd
        return reserve_cloud_quota_call(
            self.settings,
            provider=getattr(self.provider, "name", self.profile.provider_name),
            model=model or self.profile.model,
            task=self.task,
            purpose=purpose,
            prompt_tokens=prompt_tokens,
            max_completion_tokens=max_completion_tokens,
            max_cost_usd=max_cost,
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


@compaction_metrics.observe_reactive_compaction("reactive_summary")
def force_compact_for_retry(messages: list[dict[str, Any]], settings: AppSettings) -> ContextProjection:
    normalized = _normalize_messages(messages)
    session_context = _load_session_context()
    compacted, _changed = auto_compact_messages(normalized, settings, session_context=session_context)
    if compacted == normalized:
        keep_recent = max(2, int(settings.context_recent_message_limit // 2 or 2))
        compacted, _ = snip_history_if_needed(normalized, settings)
        if compacted == normalized and len(normalized) > keep_recent:
            tail = select_recent_complete_tail(normalized, keep_recent)
            compacted = [
                _system_context_message(
                    load_prompt("context_reactive_compaction.md"),
                    {"context_boundary": "reactive_compact"},
                ),
                *tail,
            ]
    compacted = repair_tool_message_invariants(compacted)
    return ContextProjection(
        messages=compacted,
        original_count=len(normalized),
        projected_count=len(compacted),
        original_tokens=count_messages_tokens(normalized),
        projected_tokens=count_messages_tokens(compacted),
        compacted=True,
        history_snipped=True,
        strategy="reactive_compact",
        source="reactive_retry",
        boundary_id=str((_latest_compact_boundary(compacted) or {}).get("id") or ""),
        compact_metadata=_compact_metadata(_latest_compact_boundary(compacted) or {}),
        retained_tail_message_ids=sorted(_retained_tail_message_ids(_latest_compact_boundary(compacted) or {})),
    )


@compaction_metrics.observe_reactive_compaction("fallback_trim")
def provider_safe_projection_fallback(
    messages: list[dict[str, Any]],
    settings: AppSettings,
    *,
    source: str = "reactive_retry_fallback",
) -> ContextProjection:
    normalized = repair_tool_message_invariants(compact_boundary_view(_normalize_messages(messages)))
    original_tokens = count_messages_tokens(normalized)
    target_tokens = _fallback_target_tokens(settings)
    compacted = _trim_oldest_unprotected_blocks(normalized, target_tokens)
    compacted = repair_tool_message_invariants(compacted)
    boundary = _latest_compact_boundary(compacted) or _latest_compact_boundary(normalized) or {}
    metadata = _compact_metadata(boundary)
    metadata.update(
        {
            "context_boundary": metadata.get("context_boundary") or "reactive_compact",
            "fallback_strategy": "trim_oldest_unprotected",
            "tokens_saved": max(0, original_tokens - count_messages_tokens(compacted)),
            "target_tokens": target_tokens,
        }
    )
    return ContextProjection(
        messages=compacted,
        original_count=len(normalized),
        projected_count=len(compacted),
        original_tokens=original_tokens,
        projected_tokens=count_messages_tokens(compacted),
        compacted=True,
        history_snipped=True,
        strategy="reactive_compact+fallback_trim",
        source=source,
        boundary_id=str(boundary.get("id") or ""),
        compact_metadata=metadata,
        retained_tail_message_ids=sorted(_retained_tail_message_ids(boundary)),
    )


def _fallback_target_tokens(settings: AppSettings) -> int:
    return _fallback_target_tokens_from_values(
        effective_context_window(settings),
        int(settings.context_manual_compact_buffer_tokens),
    )


def _safe_context_usage_snapshot(projection: ContextProjection, settings: AppSettings) -> dict[str, Any]:
    try:
        from app.context.usage import analyze_context_usage, context_usage_to_dict

        return context_usage_to_dict(
            analyze_context_usage(
                messages=projection.messages,
                settings=settings,
                include_registered_tools=False,
                include_session_memory=False,
                include_projection=True,
            )
        )
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: context usage diagnostics should not block LLM calls.
        log_best_effort_failure(logger, "context.safe_usage_snapshot", exc)
        return {"error": str(exc)}


__all__ = [
    "ContextAwareProvider",
    "LLMCapabilityError",
    "force_compact_for_retry",
    "provider_safe_projection_fallback",
]
