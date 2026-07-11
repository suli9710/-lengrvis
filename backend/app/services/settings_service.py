from __future__ import annotations

import logging
from typing import Any

from app.core import db
from app.core.errors import AppError
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.openai_compatible import circuit_snapshot
from app.llm.profiles import profile_for_provider, profile_for_settings
from app.llm.prompts import load_prompt
from app.llm.registry import _is_local_base_url, get_effective_settings, get_provider, get_provider_for_mode
from app.llm.usage import list_usage_events, usage_summary
from app.observability.best_effort import log_best_effort_failure
from app.policy.redaction import redact_text
from app.security.execution_isolation import release_execution_configuration_issues
from app.security.sensitive_confirmation import CONFIRMATION_FIELD, require_settings_confirmation

SENSITIVE_SETTINGS = {"api_key", "jwt_secret"}

# License-derived fields that must never be persisted via the settings API.
# ``plan`` is resolved at read time from ``LENGRVIS_PLAN`` / license tokens via
# ``apply_licensed_plan``; accepting it on the write path would let a
# desktop-token holder self-upgrade to Max without a valid license.
NON_PERSISTABLE_SETTINGS = {"plan"}
logger = logging.getLogger(__name__)


def get_settings() -> dict[str, Any]:
    return get_effective_settings().public_dict()


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    rejected_secrets = sorted(SENSITIVE_SETTINGS.intersection(payload))
    if rejected_secrets:
        names = ", ".join(rejected_secrets)
        raise AppError(
            "secret_settings_must_use_external_config",
            f"Sensitive settings ({names}) must be configured through environment variables or external config.",
            status_code=400,
        )
    # Silently strip license-derived fields: they are resolved from env/license
    # at read time and must never be persisted through this write path. Stripping
    # (rather than rejecting) keeps incidental payload fields from breaking
    # unrelated settings updates the frontend may send alongside.
    payload = {k: v for k, v in payload.items() if k not in NON_PERSISTABLE_SETTINGS}
    coerced = coerce_settings_patch(payload)
    _validate_settings_patch(coerced)
    require_settings_confirmation({**coerced, CONFIRMATION_FIELD: payload.get(CONFIRMATION_FIELD)})
    for key, value in coerced.items():
        db.set_setting(key, value)
    return get_settings()


def coerce_settings_patch(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(type(get_effective_settings()).model_fields)
    coerced: dict[str, Any] = {}
    for key, value in payload.items():
        if key in allowed:
            coerced[key] = _coerce_setting_value(key, value)
    return coerced


def get_llm_profile() -> dict[str, Any]:
    settings = get_effective_settings()
    try:
        provider = get_provider_for_mode(settings)
        profile = profile_for_provider(provider, settings)
        degraded = getattr(provider, "name", "") == "mock"
        error = ""
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        log_best_effort_failure(logger, "settings.llm_profile", exc, mode=settings.mode)
        profile = profile_for_settings(settings)
        degraded = True
        error = _safe_error(exc)
    return {
        "mode": settings.mode,
        "task": "default",
        "profile": profile.to_dict(),
        "degraded": degraded,
        "error": error,
    }


def get_llm_health() -> dict[str, Any]:
    settings = get_effective_settings()
    active: dict[str, Any]
    try:
        provider = get_provider_for_mode(settings)
        profile = profile_for_provider(provider, settings)
        active = {
            "available": True,
            "degraded": getattr(provider, "name", "") == "mock",
            "provider": getattr(provider, "name", profile.provider_name),
            "model": profile.model,
            "profile": profile.to_dict(),
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
        log_best_effort_failure(logger, "settings.llm_health", exc, mode=settings.mode)
        profile = profile_for_settings(settings)
        active = {
            "available": False,
            "degraded": True,
            "provider": profile.provider_name,
            "model": profile.model,
            "profile": profile.to_dict(),
            "error": _safe_error(exc),
        }
    return {
        "active": active,
        "retry": {
            "max_retries": settings.llm_api_max_retries,
            "backoff_seconds": settings.llm_api_retry_backoff_seconds,
            "circuit_failure_threshold": settings.llm_api_circuit_failure_threshold,
            "circuit_cooldown_seconds": settings.llm_api_circuit_cooldown_seconds,
            "circuit": circuit_snapshot(settings),
        },
    }


def get_llm_usage(limit: int = 100) -> dict[str, Any]:
    return {"events": list_usage_events(limit=limit)}


def get_llm_cost_summary(hours: int = 24) -> dict[str, Any]:
    return usage_summary(hours=hours)


def _coerce_setting_value(key: str, value: Any) -> Any:
    if key == "wire_api":
        candidate = str(value).strip().lower()
        return candidate if candidate in {"chat_completions", "responses"} else "chat_completions"
    if key in {"allowed_directories", "app_allowlist", "skill_directories"}:
        if isinstance(value, str):
            return [item.strip() for item in value.replace("\n", ";").split(";") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []
    int_min_one_keys = {
        "browser_max_page_bytes",
        "document_max_chars_to_llm",
        "index_rebuild_max_files",
        "index_rebuild_max_bytes",
        "llm_api_circuit_failure_threshold",
        "model_context_window",
        "model_auto_compact_token_limit",
        "context_warning_buffer_tokens",
        "context_error_buffer_tokens",
        "context_manual_compact_buffer_tokens",
        "context_session_summary_limit",
        "context_recent_message_limit",
        "context_micro_compact_age",
        "context_micro_compact_tool_result_chars",
        "context_history_snip_threshold",
        "context_history_snip_keep_recent",
        "context_min_summary_chars",
        "max_tokens",
        "timeout",
        "onnx_embedding_max_batch_size",
        "onnx_image_embedding_max_batch_size",
        "ocr_batch_size",
    }
    if key in int_min_one_keys:
        return _coerce_int_setting(key, value, minimum=1)
    if key in {
        "llm_api_max_retries",
        "recovery_max_retries",
        "os_reflection_max_per_run",
        "os_reflection_max_per_step",
    }:
        return _coerce_int_setting(key, value, minimum=0)
    float_min_zero_keys = {
        "llm_api_retry_backoff_seconds",
        "llm_api_circuit_cooldown_seconds",
        "temperature",
        "ocr_min_confidence",
        "perception_frame_diff_threshold",
    }
    if key in float_min_zero_keys:
        return _coerce_float_setting(key, value, minimum=0.0)
    if key == "tool_timeout_seconds":
        return _coerce_float_setting(key, value, minimum=1.0)
    if key in {
        "provider_name",
        "base_url",
        "api_key",
        "model",
        "review_model",
        "model_reasoning_effort",
        "network_access",
        "embedding_model",
        "vision_model",
        "onnx_provider_preference",
        "onnx_directml_device_id",
        "onnx_openvino_device",
        "onnx_openvino_cache_dir",
        "onnx_model_family",
        "onnx_model_path",
        "onnx_runtime",
        "onnx_execution_provider",
        "embedding_backend",
        "onnx_embedding_model_path",
        "onnx_embedding_execution_provider",
        "onnx_embedding_model_id",
        "image_embedding_backend",
        "onnx_image_embedding_model_path",
        "onnx_image_embedding_execution_provider",
        "onnx_image_embedding_model_id",
        "ocr_backend",
        "ocr_execution_provider",
        "ocr_openvino_model_dir",
        "ocr_openvino_device",
        "ocr_lang",
        "lan_tls_cert_file",
        "lan_tls_key_file",
        "lan_public_base_url",
        "jwt_secret",
    }:
        return str(value or "").strip()
    if key == "mode":
        candidate = str(value).strip().lower()
        if candidate not in {"privacy", "efficiency", "hybrid"}:
            return "privacy"
        return candidate
    if key == "permission_mode":
        candidate = str(value).strip().lower()
        aliases = {
            "accept_edits": "trusted_edits",
            "trusted": "trusted_edits",
            "auto": "auto_review",
            "dontask": "dont_ask",
            "deny": "dont_ask",
        }
        candidate = aliases.get(candidate, candidate)
        return candidate if candidate in {"plan", "default", "trusted_edits", "auto_review", "dont_ask"} else "default"
    if key == "mcp_servers":
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            name = str(item.get("name") or item.get("id") or "mcp").strip()
            if not url or not name:
                continue
            normalized.append(
                {
                    "name": name,
                    "url": url,
                    "transport": str(item.get("transport", "http")),
                    "enabled": bool(item.get("enabled", True)),
                }
            )
        return normalized
    if key in {
        "allow_browser_network",
        "allow_cloud_context",
        "allow_file_content_upload",
        "requires_openai_auth",
        "disable_response_storage",
        "strict_state_machine",
        "remote_desktop_enabled",
        "lan_tls_enabled",
        "allow_unsafe_local_skill_execution",
        "allow_mock_fallback",
        "onnx_enabled",
        "onnx_warm_on_startup",
        "perception_local_ocr_enabled",
        "context_auto_compact_enabled",
        "context_micro_compact_enabled",
        "context_history_snip_enabled",
        "context_session_memory_enabled",
        "memory_auto_learning_enabled",
        "developer_writes_enabled",
        "developer_writes_require_verification",
    }:
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}
    return value


def _coerce_int_setting(key: str, value: Any, *, minimum: int) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError) as exc:
        raise AppError(
            "invalid_numeric_setting",
            f"Setting '{key}' must be an integer.",
            status_code=400,
        ) from exc


def _coerce_float_setting(key: str, value: Any, *, minimum: float) -> float:
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError) as exc:
        raise AppError(
            "invalid_numeric_setting",
            f"Setting '{key}' must be a number.",
            status_code=400,
        ) from exc


def _validate_settings_patch(patch: dict[str, Any]) -> None:
    if not patch:
        return
    candidate = get_effective_settings().merged(patch)
    execution_issues = release_execution_configuration_issues(candidate)
    if execution_issues:
        raise AppError(
            "execution_isolation_required",
            " ".join(execution_issues),
            status_code=409,
        )
    provider_name = candidate.provider_name.lower()
    if provider_name in {"ollama", "lmstudio", "llamacpp", "llama.cpp", "vllm_local", "local"}:
        if candidate.base_url and not _is_local_base_url(candidate.base_url):
            raise AppError(
                "unsafe_local_llm_base_url",
                "Local LLM providers must use a loopback, private-network, or .localhost base URL.",
            )


async def test_llm_provider() -> dict[str, Any]:
    provider = None
    try:
        provider = get_provider()
        text = await provider.chat([{"role": "user", "content": load_prompt("settings_test_llm_provider.md")}])
        degraded = provider.name == "mock"
        return {"ok": not degraded, "provider": provider.name, "message": text, "degraded": degraded}
    except LocalBackendUnavailable as exc:
        return {"ok": False, "provider": "local", "error": _safe_error(exc)}
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: provider probes surface user-facing diagnostics.
        log_best_effort_failure(
            logger,
            "settings.test_llm_provider",
            exc,
            provider=getattr(provider, "name", "unknown"),
        )
        return {"ok": False, "provider": getattr(provider, "name", "unknown"), "error": _safe_error(exc)}


def _safe_error(exc: Exception) -> str:
    return redact_text(str(exc) or exc.__class__.__name__)
