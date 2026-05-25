from __future__ import annotations

import dataclasses

from app.config import AppSettings, get_base_settings
from app.core import db
from app.llm.base import LLMProvider
from app.llm.local_provider import LocalBackendUnavailable, detect_local_backend, unavailable_message
from app.llm.mock_provider import MockProvider
from app.llm.onnx_provider import OnnxProvider, detect_onnx_backend
from app.llm.openai_compatible import OpenAICompatibleProvider


CLOUD_PROVIDERS = {"openai", "openai_compatible", "deepseek", "azure_openai", "hunyuan", "custom_http"}
LOCAL_PROVIDERS = {"ollama", "lmstudio", "llamacpp", "llama.cpp", "vllm_local", "local", "onnx"}
KNOWN_TASKS = {"planner", "supervisor", "subagent", "embed", "vision", "ocr", "default"}


def get_effective_settings() -> AppSettings:
    db.init_db()
    return get_base_settings().merged(db.get_settings_overrides())


def _is_local_base_url(base_url: str) -> bool:
    base = (base_url or "").lower()
    return any(token in base for token in ("127.0.0.1", "localhost", "0.0.0.0", "://local", "://lan"))


def _build_cloud_provider(settings: AppSettings) -> LLMProvider:
    name = settings.provider_name.lower()
    if name in CLOUD_PROVIDERS:
        if not settings.api_key:
            return _fallback_or_raise(settings, reason="cloud provider without api_key")
        return OpenAICompatibleProvider(settings)
    if name in LOCAL_PROVIDERS and settings.base_url:
        return OpenAICompatibleProvider(settings)
    return _fallback_or_raise(settings, reason=f"unsupported cloud provider '{name}'")


def _build_local_provider(settings: AppSettings) -> LLMProvider:
    onnx_backend = detect_onnx_backend(settings)
    if onnx_backend is not None:
        return OnnxProvider(settings, onnx_backend)
    # Honour explicitly-configured local providers first.
    if settings.provider_name.lower() in LOCAL_PROVIDERS and settings.base_url:
        return OpenAICompatibleProvider(_local_settings(settings))
    if _is_local_base_url(settings.base_url):
        return OpenAICompatibleProvider(_local_settings(settings))
    # Auto-detect Ollama / LM Studio / llama.cpp on the local machine.
    backend = detect_local_backend()
    if backend is not None:
        overrides = dataclasses.replace(
            settings,
            provider_name=backend.kind,
            base_url=backend.base_url,
            model=settings.model or (backend.models[0] if backend.models else "qwen2.5:3b-instruct"),
            api_key=settings.api_key or "local",
            requires_openai_auth=False,
        )
        return OpenAICompatibleProvider(overrides)
    raise LocalBackendUnavailable(unavailable_message())


def _local_settings(settings: AppSettings) -> AppSettings:
    return dataclasses.replace(
        settings,
        api_key=settings.api_key or "local",
        requires_openai_auth=False,
    )


def _fallback_or_raise(settings: AppSettings, *, reason: str) -> LLMProvider:
    """Return MockProvider for non-local paths when explicitly allowed."""
    if getattr(settings, "allow_mock_fallback", True):
        return MockProvider()
    raise LocalBackendUnavailable(reason)


def get_provider_for_mode(settings: AppSettings | None = None, *, task: str = "default") -> LLMProvider:
    effective = settings or get_effective_settings()
    mode = (effective.mode or "privacy").lower()
    normalized_task = task if task in KNOWN_TASKS else "default"
    if mode == "efficiency":
        return _build_cloud_provider(effective)
    if mode == "privacy":
        return _build_local_provider(effective)
    if mode == "hybrid":
        if normalized_task in {"planner", "supervisor"}:
            return _build_cloud_provider(effective)
        if normalized_task in {"vision", "ocr"} and effective.allow_cloud_context:
            return _build_cloud_provider(effective)
        return _build_local_provider(effective)
    return _build_local_provider(effective)


def get_provider(settings: AppSettings | None = None, *, task: str = "default") -> LLMProvider:
    return get_provider_for_mode(settings, task=task)
