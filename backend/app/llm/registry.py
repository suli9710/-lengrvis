from __future__ import annotations

import dataclasses
import ipaddress
import os
import threading
import time
from urllib.parse import urlparse

from app.commerce.entitlements import Feature, active_plan, has_feature
from app.config import ENV_PREFIX, AppSettings, get_base_settings
from app.core.outbound_url import validate_outbound_http_url
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


# get_effective_settings() is called several times per orchestration step
# (tool context, provider build, planner, engine init); each uncached call
# re-reads config.yaml/.env plus the settings table. A short TTL keeps reads
# hot while writes invalidate immediately via db.set_setting().
_SETTINGS_CACHE_TTL_SECONDS = 2.0
_SETTINGS_CACHE_LOCK = threading.Lock()
_settings_cache_value: AppSettings | None = None
_settings_cache_key: tuple[tuple[str, str], ...] | None = None
_settings_cache_expires_at = 0.0


def _settings_env_fingerprint() -> tuple[tuple[str, str], ...]:
    """Capture LENGRVIS_* env state so monkeypatched env changes bypass the cache."""
    return tuple(sorted((key, value) for key, value in os.environ.items() if key.startswith(ENV_PREFIX)))


def invalidate_settings_cache() -> None:
    global _settings_cache_value, _settings_cache_key
    with _SETTINGS_CACHE_LOCK:
        _settings_cache_value = None
        _settings_cache_key = None


# Registered at import time so db can notify us on settings writes without
# importing this module (core must not depend on llm).
db.register_settings_invalidation_hook(invalidate_settings_cache)


def get_effective_settings() -> AppSettings:
    global _settings_cache_value, _settings_cache_key, _settings_cache_expires_at
    fingerprint = _settings_env_fingerprint()
    with _SETTINGS_CACHE_LOCK:
        if (
            _settings_cache_value is not None
            and _settings_cache_key == fingerprint
            and time.monotonic() < _settings_cache_expires_at
        ):
            return _settings_cache_value

    db.init_db()
    base = get_base_settings()
    persisted = base.merged(db.get_settings_overrides())
    effective = persisted.merged(_explicit_process_env_overrides(base))
    effective = _enforce_plan_entitlements(effective)

    with _SETTINGS_CACHE_LOCK:
        _settings_cache_value = effective
        _settings_cache_key = fingerprint
        _settings_cache_expires_at = time.monotonic() + _SETTINGS_CACHE_TTL_SECONDS
    return effective


def _enforce_plan_entitlements(settings: AppSettings) -> AppSettings:
    """Bind high-risk paid-tier capabilities to the active commercialization plan.

    Remote desktop control (手机远控) is a Pro/Team capability. When the active
    plan is not entitled to it we force ``remote_desktop_enabled`` off at this
    settings chokepoint, which deactivates every downstream remote-control gate
    (WebSocket authorization, input handling, session keepalive) without
    weakening the per-action strong-approval flow that still governs entitled
    plans.
    """
    if settings.remote_desktop_enabled and not has_feature(active_plan(settings), Feature.REMOTE_CONTROL):
        return dataclasses.replace(settings, remote_desktop_enabled=False)
    return settings


def _explicit_process_env_overrides(base: AppSettings) -> dict[str, object]:
    overrides: dict[str, object] = {}
    env_to_field = {
        "LENGRVIS_MODE": "mode",
        "LENGRVIS_PROVIDER_NAME": "provider_name",
        "LENGRVIS_BASE_URL": "base_url",
        "LENGRVIS_API_KEY": "api_key",
        "LENGRVIS_MODEL": "model",
        "LENGRVIS_REVIEW_MODEL": "review_model",
        "LENGRVIS_WIRE_API": "wire_api",
        "LENGRVIS_REQUIRES_OPENAI_AUTH": "requires_openai_auth",
        "LENGRVIS_MODEL_REASONING_EFFORT": "model_reasoning_effort",
        "LENGRVIS_DISABLE_RESPONSE_STORAGE": "disable_response_storage",
        "LENGRVIS_NETWORK_ACCESS": "network_access",
        "LENGRVIS_PERMISSION_MODE": "permission_mode",
        "LENGRVIS_MODEL_CONTEXT_WINDOW": "model_context_window",
        "LENGRVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT": "model_auto_compact_token_limit",
        "LENGRVIS_CONTEXT_WARNING_BUFFER_TOKENS": "context_warning_buffer_tokens",
        "LENGRVIS_CONTEXT_ERROR_BUFFER_TOKENS": "context_error_buffer_tokens",
        "LENGRVIS_CONTEXT_MANUAL_COMPACT_BUFFER_TOKENS": "context_manual_compact_buffer_tokens",
        "LENGRVIS_CONTEXT_AUTO_COMPACT_ENABLED": "context_auto_compact_enabled",
        "LENGRVIS_CONTEXT_MICRO_COMPACT_ENABLED": "context_micro_compact_enabled",
        "LENGRVIS_CONTEXT_HISTORY_SNIP_ENABLED": "context_history_snip_enabled",
        "LENGRVIS_CONTEXT_SESSION_MEMORY_ENABLED": "context_session_memory_enabled",
        "LENGRVIS_CONTEXT_SESSION_SUMMARY_LIMIT": "context_session_summary_limit",
        "LENGRVIS_CONTEXT_RECENT_MESSAGE_LIMIT": "context_recent_message_limit",
        "LENGRVIS_CONTEXT_MICRO_COMPACT_AGE": "context_micro_compact_age",
        "LENGRVIS_CONTEXT_MICRO_COMPACT_TOOL_RESULT_CHARS": "context_micro_compact_tool_result_chars",
        "LENGRVIS_CONTEXT_HISTORY_SNIP_THRESHOLD": "context_history_snip_threshold",
        "LENGRVIS_CONTEXT_HISTORY_SNIP_KEEP_RECENT": "context_history_snip_keep_recent",
        "LENGRVIS_CONTEXT_MIN_SUMMARY_CHARS": "context_min_summary_chars",
        "LENGRVIS_EMBEDDING_MODEL": "embedding_model",
        "LENGRVIS_VISION_MODEL": "vision_model",
        "LENGRVIS_ONNX_ENABLED": "onnx_enabled",
        "LENGRVIS_ONNX_MODEL_PATH": "onnx_model_path",
        "LENGRVIS_ONNX_RUNTIME": "onnx_runtime",
        "LENGRVIS_ONNX_EXECUTION_PROVIDER": "onnx_execution_provider",
        "LENGRVIS_ONNX_PROVIDER_PREFERENCE": "onnx_provider_preference",
        "LENGRVIS_ONNX_DIRECTML_DEVICE_ID": "onnx_directml_device_id",
        "LENGRVIS_ONNX_OPENVINO_DEVICE": "onnx_openvino_device",
        "LENGRVIS_ONNX_OPENVINO_CACHE_DIR": "onnx_openvino_cache_dir",
        "LENGRVIS_ONNX_WARM_ON_STARTUP": "onnx_warm_on_startup",
        "LENGRVIS_ONNX_MODEL_FAMILY": "onnx_model_family",
        "LENGRVIS_EMBEDDING_BACKEND": "embedding_backend",
        "LENGRVIS_ONNX_EMBEDDING_MODEL_PATH": "onnx_embedding_model_path",
        "LENGRVIS_ONNX_EMBEDDING_EXECUTION_PROVIDER": "onnx_embedding_execution_provider",
        "LENGRVIS_ONNX_EMBEDDING_MODEL_ID": "onnx_embedding_model_id",
        "LENGRVIS_ONNX_EMBEDDING_MAX_BATCH_SIZE": "onnx_embedding_max_batch_size",
        "LENGRVIS_IMAGE_EMBEDDING_BACKEND": "image_embedding_backend",
        "LENGRVIS_ONNX_IMAGE_EMBEDDING_MODEL_PATH": "onnx_image_embedding_model_path",
        "LENGRVIS_ONNX_IMAGE_EMBEDDING_EXECUTION_PROVIDER": "onnx_image_embedding_execution_provider",
        "LENGRVIS_ONNX_IMAGE_EMBEDDING_MODEL_ID": "onnx_image_embedding_model_id",
        "LENGRVIS_ONNX_IMAGE_EMBEDDING_MAX_BATCH_SIZE": "onnx_image_embedding_max_batch_size",
        "LENGRVIS_OCR_BACKEND": "ocr_backend",
        "LENGRVIS_OCR_EXECUTION_PROVIDER": "ocr_execution_provider",
        "LENGRVIS_OCR_OPENVINO_MODEL_DIR": "ocr_openvino_model_dir",
        "LENGRVIS_OCR_OPENVINO_DEVICE": "ocr_openvino_device",
        "LENGRVIS_OCR_LANG": "ocr_lang",
        "LENGRVIS_OCR_MIN_CONFIDENCE": "ocr_min_confidence",
        "LENGRVIS_OCR_BATCH_SIZE": "ocr_batch_size",
        "LENGRVIS_TEMPERATURE": "temperature",
        "LENGRVIS_MAX_TOKENS": "max_tokens",
        "LENGRVIS_TIMEOUT": "timeout",
        "LENGRVIS_LLM_API_MAX_RETRIES": "llm_api_max_retries",
        "LENGRVIS_LLM_API_RETRY_BACKOFF_SECONDS": "llm_api_retry_backoff_seconds",
        "LENGRVIS_LLM_API_CIRCUIT_FAILURE_THRESHOLD": "llm_api_circuit_failure_threshold",
        "LENGRVIS_LLM_API_CIRCUIT_COOLDOWN_SECONDS": "llm_api_circuit_cooldown_seconds",
        "LENGRVIS_ALLOW_CLOUD_CONTEXT": "allow_cloud_context",
        "LENGRVIS_ALLOW_FILE_CONTENT_UPLOAD": "allow_file_content_upload",
        "LENGRVIS_ALLOW_BROWSER_NETWORK": "allow_browser_network",
        "LENGRVIS_REMOTE_DESKTOP_ENABLED": "remote_desktop_enabled",
        "LENGRVIS_LAN_TLS_ENABLED": "lan_tls_enabled",
        "LENGRVIS_LAN_TLS_CERT_FILE": "lan_tls_cert_file",
        "LENGRVIS_LAN_TLS_KEY_FILE": "lan_tls_key_file",
        "LENGRVIS_LAN_PUBLIC_BASE_URL": "lan_public_base_url",
        "LENGRVIS_APP_ALLOWLIST": "app_allowlist",
        "LENGRVIS_BROWSER_MAX_PAGE_BYTES": "browser_max_page_bytes",
        "LENGRVIS_DOCUMENT_MAX_CHARS_TO_LLM": "document_max_chars_to_llm",
        "LENGRVIS_BROWSER_SCREENSHOT_DIR": "browser_screenshot_dir",
        "LENGRVIS_ALLOWED_DIRECTORIES": "allowed_directories",
        "LENGRVIS_SKILL_DIRECTORIES": "skill_directories",
        "LENGRVIS_DATA_DIR": "data_dir",
        "LENGRVIS_MCP_SERVERS": "mcp_servers",
        "LENGRVIS_ALLOW_MOCK_FALLBACK": "allow_mock_fallback",
        "LENGRVIS_STRICT_STATE_MACHINE": "strict_state_machine",
        "LENGRVIS_TOOL_TIMEOUT_SECONDS": "tool_timeout_seconds",
        "LENGRVIS_RECOVERY_MAX_RETRIES": "recovery_max_retries",
        "LENGRVIS_OS_REFLECTION_MAX_PER_RUN": "os_reflection_max_per_run",
        "LENGRVIS_OS_REFLECTION_MAX_PER_STEP": "os_reflection_max_per_step",
        "LENGRVIS_EXECUTION_ENGINES": "execution_engines",
        "LENGRVIS_DEFAULT_ENGINE": "default_engine",
        "LENGRVIS_AGENT_LOOP_MAX_TURNS": "agent_loop_max_turns",
        "LENGRVIS_RUN_EVENT_RETENTION_DAYS": "run_event_retention_days",
        "LENGRVIS_PERCEPTION_ENABLED": "perception_enabled",
        "LENGRVIS_PERCEPTION_INTERVAL_SECONDS": "perception_interval_seconds",
        "LENGRVIS_PERCEPTION_PUBLISH_EVENTS": "perception_publish_events",
        "LENGRVIS_PERCEPTION_MAX_WIDTH": "perception_max_width",
        "LENGRVIS_PERCEPTION_MAX_HEIGHT": "perception_max_height",
        "LENGRVIS_PERCEPTION_JPEG_QUALITY": "perception_jpeg_quality",
        "LENGRVIS_PERCEPTION_FRAME_DIFF_GATE_ENABLED": "perception_frame_diff_gate_enabled",
        "LENGRVIS_PERCEPTION_STORAGE_ENABLED": "perception_storage_enabled",
        "LENGRVIS_PERCEPTION_STORE_SCREENSHOTS": "perception_store_screenshots",
        "LENGRVIS_PERCEPTION_LOCAL_OCR_ENABLED": "perception_local_ocr_enabled",
        "LENGRVIS_PERCEPTION_FRAME_DIFF_THRESHOLD": "perception_frame_diff_threshold",
        "LENGRVIS_PERCEPTION_SENSITIVE_WINDOW_PATTERNS": "perception_sensitive_window_patterns",
        "LENGRVIS_ENVIRONMENT_SENSITIVE_WINDOW_TERMS": "perception_sensitive_window_patterns",
        "LENGRVIS_PERCEPTION_SENSITIVE_FIELD_NAMES": "perception_sensitive_field_names",
        "LENGRVIS_ENVIRONMENT_RULES_CONFIG": "environment_rules",
        "LENGRVIS_ENVIRONMENT_APP_CONTEXT_INTERVAL_SECONDS": "environment_app_context_interval_seconds",
        "LENGRVIS_ENVIRONMENT_STORE_SCREENSHOTS": "environment_store_screenshots",
        "LENGRVIS_ENVIRONMENT_EVENT_RETENTION_DAYS": "environment_event_retention_days",
        "LENGRVIS_JWT_SECRET": "jwt_secret",
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
        if settings.base_url:
            validate_outbound_http_url(settings.base_url, allow_private=False)
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
    if getattr(settings, "allow_mock_fallback", False):
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
