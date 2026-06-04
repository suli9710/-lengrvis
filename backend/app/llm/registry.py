from __future__ import annotations

import dataclasses
import ipaddress
import os
from urllib.parse import urlparse

from app.config import AppSettings, get_base_settings
from app.context_management import ContextAwareProvider
from app.core import db
from app.llm.profiles import profile_for_provider
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
    base = get_base_settings()
    persisted = base.merged(db.get_settings_overrides())
    return persisted.merged(_explicit_process_env_overrides(base))


def _explicit_process_env_overrides(base: AppSettings) -> dict[str, object]:
    overrides: dict[str, object] = {}
    env_to_field = {
        "MARVIS_MODE": "mode",
        "MARVIS_PROVIDER_NAME": "provider_name",
        "MARVIS_BASE_URL": "base_url",
        "MARVIS_API_KEY": "api_key",
        "MARVIS_MODEL": "model",
        "MARVIS_REVIEW_MODEL": "review_model",
        "MARVIS_WIRE_API": "wire_api",
        "MARVIS_REQUIRES_OPENAI_AUTH": "requires_openai_auth",
        "MARVIS_MODEL_REASONING_EFFORT": "model_reasoning_effort",
        "MARVIS_DISABLE_RESPONSE_STORAGE": "disable_response_storage",
        "MARVIS_NETWORK_ACCESS": "network_access",
        "MARVIS_PERMISSION_MODE": "permission_mode",
        "MARVIS_MODEL_CONTEXT_WINDOW": "model_context_window",
        "MARVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT": "model_auto_compact_token_limit",
        "MARVIS_CONTEXT_WARNING_BUFFER_TOKENS": "context_warning_buffer_tokens",
        "MARVIS_CONTEXT_ERROR_BUFFER_TOKENS": "context_error_buffer_tokens",
        "MARVIS_CONTEXT_MANUAL_COMPACT_BUFFER_TOKENS": "context_manual_compact_buffer_tokens",
        "MARVIS_CONTEXT_AUTO_COMPACT_ENABLED": "context_auto_compact_enabled",
        "MARVIS_CONTEXT_MICRO_COMPACT_ENABLED": "context_micro_compact_enabled",
        "MARVIS_CONTEXT_HISTORY_SNIP_ENABLED": "context_history_snip_enabled",
        "MARVIS_CONTEXT_SESSION_MEMORY_ENABLED": "context_session_memory_enabled",
        "MARVIS_CONTEXT_SESSION_SUMMARY_LIMIT": "context_session_summary_limit",
        "MARVIS_CONTEXT_RECENT_MESSAGE_LIMIT": "context_recent_message_limit",
        "MARVIS_CONTEXT_MICRO_COMPACT_AGE": "context_micro_compact_age",
        "MARVIS_CONTEXT_MICRO_COMPACT_TOOL_RESULT_CHARS": "context_micro_compact_tool_result_chars",
        "MARVIS_CONTEXT_HISTORY_SNIP_THRESHOLD": "context_history_snip_threshold",
        "MARVIS_CONTEXT_HISTORY_SNIP_KEEP_RECENT": "context_history_snip_keep_recent",
        "MARVIS_CONTEXT_MIN_SUMMARY_CHARS": "context_min_summary_chars",
        "MARVIS_EMBEDDING_MODEL": "embedding_model",
        "MARVIS_VISION_MODEL": "vision_model",
        "MARVIS_ONNX_ENABLED": "onnx_enabled",
        "MARVIS_ONNX_MODEL_PATH": "onnx_model_path",
        "MAVRIS_ONNX_MODEL_PATH": "onnx_model_path",
        "MARVIS_ONNX_RUNTIME": "onnx_runtime",
        "MAVRIS_ONNX_RUNTIME": "onnx_runtime",
        "MARVIS_ONNX_EXECUTION_PROVIDER": "onnx_execution_provider",
        "MAVRIS_ONNX_EXECUTION_PROVIDER": "onnx_execution_provider",
        "MARVIS_ONNX_PROVIDER_PREFERENCE": "onnx_provider_preference",
        "MARVIS_ONNX_DIRECTML_DEVICE_ID": "onnx_directml_device_id",
        "MARVIS_ONNX_OPENVINO_DEVICE": "onnx_openvino_device",
        "MARVIS_ONNX_OPENVINO_CACHE_DIR": "onnx_openvino_cache_dir",
        "MARVIS_ONNX_WARM_ON_STARTUP": "onnx_warm_on_startup",
        "MARVIS_ONNX_MODEL_FAMILY": "onnx_model_family",
        "MARVIS_EMBEDDING_BACKEND": "embedding_backend",
        "MARVIS_ONNX_EMBEDDING_MODEL_PATH": "onnx_embedding_model_path",
        "MARVIS_ONNX_EMBEDDING_EXECUTION_PROVIDER": "onnx_embedding_execution_provider",
        "MARVIS_ONNX_EMBEDDING_MODEL_ID": "onnx_embedding_model_id",
        "MARVIS_ONNX_EMBEDDING_MAX_BATCH_SIZE": "onnx_embedding_max_batch_size",
        "MARVIS_IMAGE_EMBEDDING_BACKEND": "image_embedding_backend",
        "MARVIS_ONNX_IMAGE_EMBEDDING_MODEL_PATH": "onnx_image_embedding_model_path",
        "MARVIS_ONNX_IMAGE_EMBEDDING_EXECUTION_PROVIDER": "onnx_image_embedding_execution_provider",
        "MARVIS_ONNX_IMAGE_EMBEDDING_MODEL_ID": "onnx_image_embedding_model_id",
        "MARVIS_ONNX_IMAGE_EMBEDDING_MAX_BATCH_SIZE": "onnx_image_embedding_max_batch_size",
        "MARVIS_OCR_BACKEND": "ocr_backend",
        "MARVIS_OCR_EXECUTION_PROVIDER": "ocr_execution_provider",
        "MARVIS_OCR_OPENVINO_MODEL_DIR": "ocr_openvino_model_dir",
        "MARVIS_OCR_OPENVINO_DEVICE": "ocr_openvino_device",
        "MARVIS_OCR_LANG": "ocr_lang",
        "MARVIS_OCR_MIN_CONFIDENCE": "ocr_min_confidence",
        "MARVIS_OCR_BATCH_SIZE": "ocr_batch_size",
        "MARVIS_TEMPERATURE": "temperature",
        "MARVIS_MAX_TOKENS": "max_tokens",
        "MARVIS_TIMEOUT": "timeout",
        "MARVIS_LLM_API_MAX_RETRIES": "llm_api_max_retries",
        "MARVIS_LLM_API_RETRY_BACKOFF_SECONDS": "llm_api_retry_backoff_seconds",
        "MARVIS_LLM_API_CIRCUIT_FAILURE_THRESHOLD": "llm_api_circuit_failure_threshold",
        "MARVIS_LLM_API_CIRCUIT_COOLDOWN_SECONDS": "llm_api_circuit_cooldown_seconds",
        "MARVIS_ALLOW_CLOUD_CONTEXT": "allow_cloud_context",
        "MARVIS_ALLOW_FILE_CONTENT_UPLOAD": "allow_file_content_upload",
        "MARVIS_ALLOW_BROWSER_NETWORK": "allow_browser_network",
        "MARVIS_REMOTE_DESKTOP_ENABLED": "remote_desktop_enabled",
        "MARVIS_APP_ALLOWLIST": "app_allowlist",
        "MARVIS_BROWSER_MAX_PAGE_BYTES": "browser_max_page_bytes",
        "MARVIS_DOCUMENT_MAX_CHARS_TO_LLM": "document_max_chars_to_llm",
        "MARVIS_BROWSER_SCREENSHOT_DIR": "browser_screenshot_dir",
        "MARVIS_ALLOWED_DIRECTORIES": "allowed_directories",
        "MARVIS_SKILL_DIRECTORIES": "skill_directories",
        "MARVIS_DATA_DIR": "data_dir",
        "MARVIS_MCP_SERVERS": "mcp_servers",
        "MARVIS_ALLOW_MOCK_FALLBACK": "allow_mock_fallback",
        "MARVIS_STRICT_STATE_MACHINE": "strict_state_machine",
        "MARVIS_RECOVERY_MAX_RETRIES": "recovery_max_retries",
        "MARVIS_EXECUTION_ENGINES": "execution_engines",
        "MAVRIS_EXECUTION_ENGINES": "execution_engines",
        "MARVIS_DEFAULT_ENGINE": "default_engine",
        "MAVRIS_DEFAULT_ENGINE": "default_engine",
        "MARVIS_AGENT_LOOP_MAX_TURNS": "agent_loop_max_turns",
        "MARVIS_RUN_EVENT_RETENTION_DAYS": "run_event_retention_days",
        "MARVIS_PERCEPTION_ENABLED": "perception_enabled",
        "MARVIS_PERCEPTION_INTERVAL_SECONDS": "perception_interval_seconds",
        "MARVIS_PERCEPTION_PUBLISH_EVENTS": "perception_publish_events",
        "MARVIS_PERCEPTION_MAX_WIDTH": "perception_max_width",
        "MARVIS_PERCEPTION_MAX_HEIGHT": "perception_max_height",
        "MARVIS_PERCEPTION_JPEG_QUALITY": "perception_jpeg_quality",
        "MARVIS_PERCEPTION_FRAME_DIFF_GATE_ENABLED": "perception_frame_diff_gate_enabled",
        "MARVIS_PERCEPTION_STORAGE_ENABLED": "perception_storage_enabled",
        "MARVIS_PERCEPTION_STORE_SCREENSHOTS": "perception_store_screenshots",
        "MARVIS_PERCEPTION_LOCAL_OCR_ENABLED": "perception_local_ocr_enabled",
        "MARVIS_PERCEPTION_FRAME_DIFF_THRESHOLD": "perception_frame_diff_threshold",
        "MARVIS_PERCEPTION_SENSITIVE_WINDOW_PATTERNS": "perception_sensitive_window_patterns",
        "MARVIS_ENVIRONMENT_SENSITIVE_WINDOW_TERMS": "perception_sensitive_window_patterns",
        "MARVIS_PERCEPTION_SENSITIVE_FIELD_NAMES": "perception_sensitive_field_names",
        "MARVIS_ENVIRONMENT_RULES_CONFIG": "environment_rules",
        "MARVIS_ENVIRONMENT_APP_CONTEXT_INTERVAL_SECONDS": "environment_app_context_interval_seconds",
        "MARVIS_ENVIRONMENT_STORE_SCREENSHOTS": "environment_store_screenshots",
        "MARVIS_ENVIRONMENT_EVENT_RETENTION_DAYS": "environment_event_retention_days",
        "MARVIS_JWT_SECRET": "jwt_secret",
        "MAVRIS_JWT_SECRET": "jwt_secret",
    }
    for env_key, field_name in env_to_field.items():
        if env_key in os.environ and hasattr(base, field_name):
            overrides[field_name] = getattr(base, field_name)
    return overrides


def _is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url or "")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost"} or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


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
    if settings.provider_name.lower() in LOCAL_PROVIDERS and settings.base_url and _is_local_base_url(settings.base_url):
        return OpenAICompatibleProvider(_local_settings(settings))
    if settings.provider_name.lower() in LOCAL_PROVIDERS and settings.base_url:
        raise LocalBackendUnavailable(
            f"Configured local provider '{settings.provider_name}' has a non-local base_url and was blocked."
        )
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
    mode = (effective.mode or "efficiency").lower()
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
    return _build_cloud_provider(effective)


def get_provider(settings: AppSettings | None = None, *, task: str = "default") -> LLMProvider:
    effective = settings or get_effective_settings()
    provider = get_provider_for_mode(effective, task=task)
    return ContextAwareProvider(provider, effective, task=task, profile=profile_for_provider(provider, effective))
