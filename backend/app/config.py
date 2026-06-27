from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config_normalization import (
    normalize_mcp_servers as _normalize_mcp_servers,
)
from app.config_normalization import (
    normalize_permission_mode as _normalize_permission_mode,
)
from app.config_normalization import (
    normalize_skill_trusted_public_keys as _normalize_skill_trusted_public_keys,
)
from app.config_normalization import (
    redact_secret_fields as _redact_secret_fields,
)
from app.config_paths import (
    APP_ROOT,
    CONFIG_PARENT_SEARCH_DEPTH,
    DEFAULT_DATA_DIR,
    DPAPI_PREFIX,
    ENV_PREFIX,
    MOBILE_JWT_SECRET_ENV_KEYS,
    MOBILE_JWT_SECRET_FILE,
    PROJECT_ROOT,
)
from app.config_sources import (
    candidate_config_dirs as _candidate_config_dirs,
)
from app.config_sources import (
    configured as _configured,
)
from app.config_sources import (
    decrypt_windows_dpapi as _decrypt_windows_dpapi,
)
from app.config_sources import (
    env_aliases,
    env_flag,
    env_raw,
    env_value,
    get_env,
)
from app.config_sources import (
    external_data_dir as _external_data_dir,
)
from app.config_sources import (
    find_config_file as _find_config_file,
)
from app.config_sources import (
    load_dotenv as _load_dotenv,
)
from app.config_sources import (
    load_yaml as _load_yaml,
)
from app.config_sources import (
    local_mobile_jwt_secret as _local_mobile_jwt_secret,
)
from app.config_sources import (
    preferred_data_dir as _preferred_data_dir,
)
from app.config_sources import (
    resolve_mobile_jwt_secret as _resolve_mobile_jwt_secret,
)

__all__ = [
    "APP_ROOT",
    "CONFIG_PARENT_SEARCH_DEPTH",
    "DEFAULT_DATA_DIR",
    "DPAPI_PREFIX",
    "ENV_PREFIX",
    "MOBILE_JWT_SECRET_ENV_KEYS",
    "MOBILE_JWT_SECRET_FILE",
    "PROJECT_ROOT",
    "AppSettings",
    "env_aliases",
    "env_flag",
    "env_raw",
    "env_value",
    "get_base_settings",
    "get_env",
    "_candidate_config_dirs",
    "_configured",
    "_decrypt_windows_dpapi",
    "_external_data_dir",
    "_find_config_file",
    "_load_dotenv",
    "_load_yaml",
    "_local_mobile_jwt_secret",
    "_normalize_mcp_servers",
    "_normalize_permission_mode",
    "_normalize_skill_trusted_public_keys",
    "_preferred_data_dir",
    "_redact_secret_fields",
    "_resolve_api_key",
    "_resolve_mobile_jwt_secret",
]


def _resolve_api_key(raw_plain: Any, raw_encrypted: Any) -> str:
    plain = str(raw_plain or "").strip()
    if plain:
        return plain
    encrypted = str(raw_encrypted or "").strip()
    if not encrypted:
        return ""
    return _decrypt_windows_dpapi(encrypted)


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LENGRVIS_",
        extra="ignore",
        protected_namespaces=(),
        validate_assignment=True,
    )

    provider_name: str = "openai_compatible"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    review_model: str = ""
    wire_api: str = "chat_completions"
    structured_output_mode: str = "auto"
    structured_output_repair_retries: int = 1
    requires_openai_auth: bool = True
    model_reasoning_effort: str = "medium"
    disable_response_storage: bool = False
    network_access: str = "disabled"
    model_context_window: int = 128000
    model_auto_compact_token_limit: int = 96000
    context_warning_buffer_tokens: int = 20000
    context_error_buffer_tokens: int = 20000
    context_manual_compact_buffer_tokens: int = 3000
    context_auto_compact_enabled: bool = True
    context_micro_compact_enabled: bool = True
    context_history_snip_enabled: bool = True
    context_session_memory_enabled: bool = True
    context_session_summary_limit: int = 12000
    context_recent_message_limit: int = 24
    context_micro_compact_age: int = 8
    context_micro_compact_tool_result_chars: int = 1200
    context_history_snip_threshold: int = 160
    context_history_snip_keep_recent: int = 80
    context_min_summary_chars: int = 1200
    embedding_model: str = "text-embedding-3-small"
    vision_model: str = ""
    onnx_enabled: bool = True
    onnx_model_path: str = ""
    onnx_runtime: str = "auto"
    onnx_execution_provider: str = ""
    onnx_provider_preference: str = "winml,directml,openvino,cpu"
    onnx_directml_device_id: str = ""
    onnx_openvino_device: str = "AUTO"
    onnx_openvino_cache_dir: str = ""
    onnx_warm_on_startup: bool = False
    onnx_model_family: str = ""
    embedding_backend: str = "auto"
    onnx_embedding_model_path: str = ""
    onnx_embedding_execution_provider: str = ""
    onnx_embedding_model_id: str = "intfloat/multilingual-e5-small"
    onnx_embedding_max_batch_size: int = 32
    image_embedding_backend: str = "auto"
    onnx_image_embedding_model_path: str = ""
    onnx_image_embedding_execution_provider: str = ""
    onnx_image_embedding_model_id: str = "openai/clip-vit-base-patch32"
    onnx_image_embedding_max_batch_size: int = 8
    ocr_backend: str = "auto"
    ocr_execution_provider: str = ""
    ocr_openvino_model_dir: str = ""
    ocr_openvino_device: str = "AUTO"
    ocr_lang: str = "multi"
    ocr_min_confidence: float = 0.0
    ocr_batch_size: int = 1
    temperature: float = 0.2
    max_tokens: int = 1600
    timeout: int = 30
    llm_api_max_retries: int = 2
    llm_api_retry_backoff_seconds: float = 0.25
    llm_api_circuit_failure_threshold: int = 5
    llm_api_circuit_cooldown_seconds: float = 30.0
    mode: str = "efficiency"
    plan: str = "free"
    permission_mode: str = "default"
    allow_cloud_context: bool = False
    allow_file_content_upload: bool = False
    allow_browser_network: bool = False
    allow_unsafe_local_skill_execution: bool = False
    remote_desktop_enabled: bool = False
    lan_tls_enabled: bool = False
    lan_tls_cert_file: str = ""
    lan_tls_key_file: str = ""
    lan_public_base_url: str = ""
    app_allowlist: list[str] = Field(default_factory=list)
    browser_max_page_bytes: int = 250000
    document_max_chars_to_llm: int = 30000
    index_rebuild_max_files: int = 25000
    index_rebuild_max_bytes: int = 2 * 1024 * 1024 * 1024
    browser_screenshot_dir: str = str(DEFAULT_DATA_DIR / "browser_screenshots")
    allowed_directories: list[str] = Field(default_factory=list)
    data_dir: str = str(DEFAULT_DATA_DIR)
    skill_directories: list[str] = Field(default_factory=list)
    skill_trusted_public_keys: dict[str, str] = Field(default_factory=dict)
    mcp_servers: list[dict] = Field(default_factory=list)
    allow_mock_fallback: bool = False
    # Non-strict (default): invalid transitions are audited but not persisted.
    # Strict: StateTransitionError on illegal phase/stage changes.
    strict_state_machine: bool = False
    tool_timeout_seconds: float = 300.0
    recovery_max_retries: int = 3
    os_reflection_max_per_run: int = 2
    os_reflection_max_per_step: int = 1
    execution_engines: str = "dual"
    default_engine: str = "auto"
    agent_loop_max_turns: int = 30
    developer_writes_enabled: bool = False  # When True, write-intent goals route to Developer Engine with verification.
    developer_writes_require_verification: bool = True
    run_event_retention_days: int = 30
    perception_enabled: bool = False
    perception_interval_seconds: float = 5.0
    perception_publish_events: bool = True
    perception_max_width: int = 1280
    perception_max_height: int = 720
    perception_jpeg_quality: int = 70
    perception_frame_diff_gate_enabled: bool = True
    perception_storage_enabled: bool = True
    perception_store_screenshots: bool = False
    perception_local_ocr_enabled: bool = False
    perception_frame_diff_threshold: float = 0.001
    perception_sensitive_window_patterns: list[str] = Field(default_factory=list)
    perception_sensitive_field_names: list[str] = Field(default_factory=list)
    environment_app_context_interval_seconds: float = 2.0
    environment_store_screenshots: bool = False
    environment_event_retention_days: int = 7
    environment_rules: list[dict[str, Any]] = Field(default_factory=list)
    # Opt-in, local-only metrics aggregation (task success / recovery / ask_user /
    # LLM anomaly rates). Never leaves the machine; disabled by default.
    local_metrics_enabled: bool = False
    jwt_secret: str = ""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (init_settings,)

    @classmethod
    def from_sources(cls) -> AppSettings:
        config_path = _find_config_file("config.yaml", "LENGRVIS_CONFIG_FILE")
        env_path = _find_config_file(".env", "LENGRVIS_ENV_FILE")
        config = _load_yaml(config_path) if config_path else {}
        env_file = _load_dotenv(env_path) if env_path else {}
        env = {**env_file, **os.environ}
        default_data_dir = _external_data_dir(config_path, env_path)

        llm = config.get("llm", {}) if isinstance(config.get("llm"), dict) else {}
        privacy = config.get("privacy", {}) if isinstance(config.get("privacy"), dict) else {}
        paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
        orchestration = config.get("orchestration", {}) if isinstance(config.get("orchestration"), dict) else {}
        perception = config.get("perception", {}) if isinstance(config.get("perception"), dict) else {}
        transport = config.get("transport", {}) if isinstance(config.get("transport"), dict) else {}

        def _section_value(section: dict[str, Any], yaml_key: str) -> tuple[bool, Any]:
            if yaml_key not in section:
                return False, None
            raw = section.get(yaml_key)
            return _configured(raw), raw

        def value(env_key: str, yaml_key: str, default: Any) -> Any:
            raw_env = env_value(env, env_key)
            if _configured(raw_env):
                return raw_env
            for section in (llm, privacy, paths, orchestration, perception, transport):
                found, raw = _section_value(section, yaml_key)
                if found:
                    return raw
            return default

        def value_any(env_keys: tuple[str, ...], yaml_key: str, default: Any) -> Any:
            for env_key in env_keys:
                raw = env_value(env, env_key)
                if _configured(raw):
                    return raw
            for section in (llm, privacy, paths, orchestration, perception, transport):
                found, raw = _section_value(section, yaml_key)
                if found:
                    return raw
            return default

        def flag(env_key: str, yaml_key: str, default: bool) -> bool:
            raw = value(env_key, yaml_key, str(default).lower())
            if isinstance(raw, bool):
                return raw
            return str(raw).lower() in {"1", "true", "yes", "on"}

        def int_value(env_key: str, yaml_key: str, default: int, *, minimum: int = 0) -> int:
            try:
                return max(minimum, int(value(env_key, yaml_key, default)))
            except (TypeError, ValueError):
                return default

        def float_value(env_key: str, yaml_key: str, default: float, *, minimum: float = 0.0) -> float:
            try:
                return max(minimum, float(value(env_key, yaml_key, default)))
            except (TypeError, ValueError):
                return default

        allowed = value("LENGRVIS_ALLOWED_DIRECTORIES", "allowed_directories", [])
        if isinstance(allowed, str):
            allowed_dirs = [p.strip() for p in allowed.split(";") if p.strip()]
        elif isinstance(allowed, list):
            allowed_dirs = [str(p) for p in allowed]
        else:
            allowed_dirs = []

        skill_directories = value("LENGRVIS_SKILL_DIRECTORIES", "skill_directories", [])
        if isinstance(skill_directories, str):
            skill_dirs = [p.strip() for p in skill_directories.split(";") if p.strip()]
        elif isinstance(skill_directories, list):
            skill_dirs = [str(p) for p in skill_directories if str(p).strip()]
        else:
            skill_dirs = []

        app_allowlist = value("LENGRVIS_APP_ALLOWLIST", "app_allowlist", ["notepad", "calculator", "calc"])
        if isinstance(app_allowlist, str):
            app_allowlist_items = [item.strip().lower() for item in app_allowlist.split(";") if item.strip()]
        elif isinstance(app_allowlist, list):
            app_allowlist_items = [str(item).strip().lower() for item in app_allowlist if str(item).strip()]
        else:
            app_allowlist_items = ["notepad", "calculator", "calc"]

        sensitive_window_patterns = value(
            "LENGRVIS_PERCEPTION_SENSITIVE_WINDOW_PATTERNS",
            "sensitive_window_patterns",
            [],
        )
        if not sensitive_window_patterns:
            sensitive_window_patterns = value(
                "LENGRVIS_ENVIRONMENT_SENSITIVE_WINDOW_TERMS",
                "environment_sensitive_window_terms",
                [],
            )
        if isinstance(sensitive_window_patterns, str):
            sensitive_window_items = [item.strip() for item in sensitive_window_patterns.split(";") if item.strip()]
        elif isinstance(sensitive_window_patterns, list):
            sensitive_window_items = [str(item).strip() for item in sensitive_window_patterns if str(item).strip()]
        else:
            sensitive_window_items = []

        sensitive_field_names = value(
            "LENGRVIS_PERCEPTION_SENSITIVE_FIELD_NAMES",
            "sensitive_field_names",
            [],
        )
        if isinstance(sensitive_field_names, str):
            sensitive_field_items = [item.strip() for item in sensitive_field_names.split(";") if item.strip()]
        elif isinstance(sensitive_field_names, list):
            sensitive_field_items = [str(item).strip() for item in sensitive_field_names if str(item).strip()]
        else:
            sensitive_field_items = []

        environment_rules = value("LENGRVIS_ENVIRONMENT_RULES_CONFIG", "environment_rules", [])
        if not isinstance(environment_rules, list):
            environment_rules = []

        api_key = _resolve_api_key(
            value("LENGRVIS_API_KEY", "api_key", ""),
            value("LENGRVIS_API_KEY_ENCRYPTED", "api_key_encrypted", ""),
        )
        data_dir = str(value("LENGRVIS_DATA_DIR", "data_dir", default_data_dir))
        jwt_secret = _resolve_mobile_jwt_secret(value_any(MOBILE_JWT_SECRET_ENV_KEYS, "jwt_secret", ""), data_dir)

        return cls(
            provider_name=str(value("LENGRVIS_PROVIDER_NAME", "provider_name", "openai_compatible")),
            base_url=str(value("LENGRVIS_BASE_URL", "base_url", "https://api.openai.com/v1")),
            api_key=api_key,
            model=str(value("LENGRVIS_MODEL", "model", "gpt-4o-mini")),
            review_model=str(value("LENGRVIS_REVIEW_MODEL", "review_model", "")),
            wire_api=str(value("LENGRVIS_WIRE_API", "wire_api", "chat_completions")),
            structured_output_mode=str(value("LENGRVIS_STRUCTURED_OUTPUT_MODE", "structured_output_mode", "auto")),
            structured_output_repair_retries=int_value(
                "LENGRVIS_STRUCTURED_OUTPUT_REPAIR_RETRIES", "structured_output_repair_retries", 1, minimum=0
            ),
            requires_openai_auth=flag("LENGRVIS_REQUIRES_OPENAI_AUTH", "requires_openai_auth", True),
            model_reasoning_effort=str(value("LENGRVIS_MODEL_REASONING_EFFORT", "model_reasoning_effort", "medium")),
            disable_response_storage=flag("LENGRVIS_DISABLE_RESPONSE_STORAGE", "disable_response_storage", False),
            network_access=str(value("LENGRVIS_NETWORK_ACCESS", "network_access", "disabled")),
            model_context_window=int_value("LENGRVIS_MODEL_CONTEXT_WINDOW", "model_context_window", 128000, minimum=1),
            model_auto_compact_token_limit=int_value(
                "LENGRVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT", "model_auto_compact_token_limit", 96000
            ),
            context_warning_buffer_tokens=int_value(
                "LENGRVIS_CONTEXT_WARNING_BUFFER_TOKENS", "context_warning_buffer_tokens", 20000
            ),
            context_error_buffer_tokens=int_value(
                "LENGRVIS_CONTEXT_ERROR_BUFFER_TOKENS", "context_error_buffer_tokens", 20000
            ),
            context_manual_compact_buffer_tokens=int_value(
                "LENGRVIS_CONTEXT_MANUAL_COMPACT_BUFFER_TOKENS", "context_manual_compact_buffer_tokens", 3000
            ),
            context_auto_compact_enabled=flag(
                "LENGRVIS_CONTEXT_AUTO_COMPACT_ENABLED", "context_auto_compact_enabled", True
            ),
            context_micro_compact_enabled=flag(
                "LENGRVIS_CONTEXT_MICRO_COMPACT_ENABLED", "context_micro_compact_enabled", True
            ),
            context_history_snip_enabled=flag(
                "LENGRVIS_CONTEXT_HISTORY_SNIP_ENABLED", "context_history_snip_enabled", True
            ),
            context_session_memory_enabled=flag(
                "LENGRVIS_CONTEXT_SESSION_MEMORY_ENABLED", "context_session_memory_enabled", True
            ),
            context_session_summary_limit=int_value(
                "LENGRVIS_CONTEXT_SESSION_SUMMARY_LIMIT", "context_session_summary_limit", 12000
            ),
            context_recent_message_limit=int_value(
                "LENGRVIS_CONTEXT_RECENT_MESSAGE_LIMIT", "context_recent_message_limit", 24, minimum=1
            ),
            context_micro_compact_age=int_value("LENGRVIS_CONTEXT_MICRO_COMPACT_AGE", "context_micro_compact_age", 8),
            context_micro_compact_tool_result_chars=int_value(
                "LENGRVIS_CONTEXT_MICRO_COMPACT_TOOL_RESULT_CHARS", "context_micro_compact_tool_result_chars", 1200
            ),
            context_history_snip_threshold=int_value(
                "LENGRVIS_CONTEXT_HISTORY_SNIP_THRESHOLD", "context_history_snip_threshold", 160
            ),
            context_history_snip_keep_recent=int_value(
                "LENGRVIS_CONTEXT_HISTORY_SNIP_KEEP_RECENT", "context_history_snip_keep_recent", 80, minimum=1
            ),
            context_min_summary_chars=int_value(
                "LENGRVIS_CONTEXT_MIN_SUMMARY_CHARS", "context_min_summary_chars", 1200
            ),
            embedding_model=str(value("LENGRVIS_EMBEDDING_MODEL", "embedding_model", "text-embedding-3-small")),
            vision_model=str(value("LENGRVIS_VISION_MODEL", "vision_model", "")),
            onnx_enabled=flag("LENGRVIS_ONNX_ENABLED", "onnx_enabled", True),
            onnx_model_path=str(value("LENGRVIS_ONNX_MODEL_PATH", "onnx_model_path", "")),
            onnx_runtime=str(value("LENGRVIS_ONNX_RUNTIME", "onnx_runtime", "auto")),
            onnx_execution_provider=str(value("LENGRVIS_ONNX_EXECUTION_PROVIDER", "onnx_execution_provider", "")),
            onnx_provider_preference=str(
                value("LENGRVIS_ONNX_PROVIDER_PREFERENCE", "onnx_provider_preference", "winml,directml,openvino,cpu")
            ),
            onnx_directml_device_id=str(value("LENGRVIS_ONNX_DIRECTML_DEVICE_ID", "onnx_directml_device_id", "")),
            onnx_openvino_device=str(value("LENGRVIS_ONNX_OPENVINO_DEVICE", "onnx_openvino_device", "AUTO")),
            onnx_openvino_cache_dir=str(value("LENGRVIS_ONNX_OPENVINO_CACHE_DIR", "onnx_openvino_cache_dir", "")),
            onnx_warm_on_startup=flag("LENGRVIS_ONNX_WARM_ON_STARTUP", "onnx_warm_on_startup", False),
            onnx_model_family=str(value("LENGRVIS_ONNX_MODEL_FAMILY", "onnx_model_family", "")),
            embedding_backend=str(value("LENGRVIS_EMBEDDING_BACKEND", "embedding_backend", "auto")),
            onnx_embedding_model_path=str(value("LENGRVIS_ONNX_EMBEDDING_MODEL_PATH", "onnx_embedding_model_path", "")),
            onnx_embedding_execution_provider=str(
                value("LENGRVIS_ONNX_EMBEDDING_EXECUTION_PROVIDER", "onnx_embedding_execution_provider", "")
            ),
            onnx_embedding_model_id=str(
                value("LENGRVIS_ONNX_EMBEDDING_MODEL_ID", "onnx_embedding_model_id", "intfloat/multilingual-e5-small")
            ),
            onnx_embedding_max_batch_size=int_value(
                "LENGRVIS_ONNX_EMBEDDING_MAX_BATCH_SIZE",
                "onnx_embedding_max_batch_size",
                32,
                minimum=1,
            ),
            image_embedding_backend=str(value("LENGRVIS_IMAGE_EMBEDDING_BACKEND", "image_embedding_backend", "auto")),
            onnx_image_embedding_model_path=str(
                value("LENGRVIS_ONNX_IMAGE_EMBEDDING_MODEL_PATH", "onnx_image_embedding_model_path", "")
            ),
            onnx_image_embedding_execution_provider=str(
                value("LENGRVIS_ONNX_IMAGE_EMBEDDING_EXECUTION_PROVIDER", "onnx_image_embedding_execution_provider", "")
            ),
            onnx_image_embedding_model_id=str(
                value(
                    "LENGRVIS_ONNX_IMAGE_EMBEDDING_MODEL_ID",
                    "onnx_image_embedding_model_id",
                    "openai/clip-vit-base-patch32",
                )
            ),
            onnx_image_embedding_max_batch_size=int_value(
                "LENGRVIS_ONNX_IMAGE_EMBEDDING_MAX_BATCH_SIZE",
                "onnx_image_embedding_max_batch_size",
                8,
                minimum=1,
            ),
            ocr_backend=str(value("LENGRVIS_OCR_BACKEND", "ocr_backend", "auto")),
            ocr_execution_provider=str(value("LENGRVIS_OCR_EXECUTION_PROVIDER", "ocr_execution_provider", "")),
            ocr_openvino_model_dir=str(value("LENGRVIS_OCR_OPENVINO_MODEL_DIR", "ocr_openvino_model_dir", "")),
            ocr_openvino_device=str(value("LENGRVIS_OCR_OPENVINO_DEVICE", "ocr_openvino_device", "AUTO")),
            ocr_lang=str(value("LENGRVIS_OCR_LANG", "ocr_lang", "multi")),
            ocr_min_confidence=float_value("LENGRVIS_OCR_MIN_CONFIDENCE", "ocr_min_confidence", 0.0),
            ocr_batch_size=int_value("LENGRVIS_OCR_BATCH_SIZE", "ocr_batch_size", 1, minimum=1),
            temperature=float_value("LENGRVIS_TEMPERATURE", "temperature", 0.2),
            max_tokens=int_value("LENGRVIS_MAX_TOKENS", "max_tokens", 1600, minimum=1),
            timeout=int_value("LENGRVIS_TIMEOUT", "timeout", 30, minimum=1),
            llm_api_max_retries=int_value("LENGRVIS_LLM_API_MAX_RETRIES", "llm_api_max_retries", 2),
            llm_api_retry_backoff_seconds=float_value(
                "LENGRVIS_LLM_API_RETRY_BACKOFF_SECONDS",
                "llm_api_retry_backoff_seconds",
                0.25,
            ),
            llm_api_circuit_failure_threshold=int_value(
                "LENGRVIS_LLM_API_CIRCUIT_FAILURE_THRESHOLD",
                "llm_api_circuit_failure_threshold",
                5,
            ),
            llm_api_circuit_cooldown_seconds=float_value(
                "LENGRVIS_LLM_API_CIRCUIT_COOLDOWN_SECONDS",
                "llm_api_circuit_cooldown_seconds",
                30.0,
            ),
            mode=str(value("LENGRVIS_MODE", "mode", "efficiency")),
            plan=str(value("LENGRVIS_PLAN", "plan", "free")).strip().lower() or "free",
            permission_mode=_normalize_permission_mode(value("LENGRVIS_PERMISSION_MODE", "permission_mode", "default")),
            allow_cloud_context=flag("LENGRVIS_ALLOW_CLOUD_CONTEXT", "allow_cloud_context", False),
            allow_file_content_upload=flag("LENGRVIS_ALLOW_FILE_CONTENT_UPLOAD", "allow_file_content_upload", False),
            allow_browser_network=flag("LENGRVIS_ALLOW_BROWSER_NETWORK", "allow_browser_network", False),
            allow_unsafe_local_skill_execution=flag(
                "LENGRVIS_ALLOW_UNSAFE_LOCAL_SKILL_EXECUTION",
                "allow_unsafe_local_skill_execution",
                False,
            ),
            remote_desktop_enabled=flag("LENGRVIS_REMOTE_DESKTOP_ENABLED", "remote_desktop_enabled", False),
            lan_tls_enabled=flag("LENGRVIS_LAN_TLS_ENABLED", "lan_tls_enabled", False),
            lan_tls_cert_file=str(value("LENGRVIS_LAN_TLS_CERT_FILE", "lan_tls_cert_file", "")),
            lan_tls_key_file=str(value("LENGRVIS_LAN_TLS_KEY_FILE", "lan_tls_key_file", "")),
            lan_public_base_url=str(value("LENGRVIS_LAN_PUBLIC_BASE_URL", "lan_public_base_url", "")),
            app_allowlist=app_allowlist_items,
            browser_max_page_bytes=int_value(
                "LENGRVIS_BROWSER_MAX_PAGE_BYTES",
                "browser_max_page_bytes",
                250000,
                minimum=1,
            ),
            document_max_chars_to_llm=int_value(
                "LENGRVIS_DOCUMENT_MAX_CHARS_TO_LLM",
                "document_max_chars_to_llm",
                30000,
                minimum=1,
            ),
            index_rebuild_max_files=int_value(
                "LENGRVIS_INDEX_REBUILD_MAX_FILES",
                "index_rebuild_max_files",
                25000,
                minimum=1,
            ),
            index_rebuild_max_bytes=int_value(
                "LENGRVIS_INDEX_REBUILD_MAX_BYTES",
                "index_rebuild_max_bytes",
                2 * 1024 * 1024 * 1024,
                minimum=1,
            ),
            browser_screenshot_dir=str(
                value(
                    "LENGRVIS_BROWSER_SCREENSHOT_DIR",
                    "browser_screenshot_dir",
                    default_data_dir / "browser_screenshots",
                )
            ),
            allowed_directories=allowed_dirs,
            data_dir=data_dir,
            skill_directories=skill_dirs,
            skill_trusted_public_keys=_normalize_skill_trusted_public_keys(
                value("LENGRVIS_SKILL_TRUSTED_PUBLIC_KEYS", "skill_trusted_public_keys", {})
            ),
            mcp_servers=_normalize_mcp_servers(value("LENGRVIS_MCP_SERVERS", "mcp_servers", [])),
            allow_mock_fallback=flag("LENGRVIS_ALLOW_MOCK_FALLBACK", "allow_mock_fallback", False),
            strict_state_machine=flag("LENGRVIS_STRICT_STATE_MACHINE", "strict_state_machine", False),
            tool_timeout_seconds=float_value(
                "LENGRVIS_TOOL_TIMEOUT_SECONDS",
                "tool_timeout_seconds",
                300.0,
                minimum=1.0,
            ),
            recovery_max_retries=int_value("LENGRVIS_RECOVERY_MAX_RETRIES", "recovery_max_retries", 3),
            os_reflection_max_per_run=int_value("LENGRVIS_OS_REFLECTION_MAX_PER_RUN", "os_reflection_max_per_run", 2),
            os_reflection_max_per_step=int_value(
                "LENGRVIS_OS_REFLECTION_MAX_PER_STEP", "os_reflection_max_per_step", 1
            ),
            execution_engines=str(value("LENGRVIS_EXECUTION_ENGINES", "execution_engines", "dual")),
            default_engine=str(value("LENGRVIS_DEFAULT_ENGINE", "default_engine", "auto")),
            agent_loop_max_turns=int_value("LENGRVIS_AGENT_LOOP_MAX_TURNS", "agent_loop_max_turns", 30, minimum=1),
            developer_writes_enabled=flag(
                "LENGRVIS_DEVELOPER_WRITES_ENABLED",
                "developer_writes_enabled",
                False,
            ),
            developer_writes_require_verification=flag(
                "LENGRVIS_DEVELOPER_WRITES_REQUIRE_VERIFICATION",
                "developer_writes_require_verification",
                True,
            ),
            run_event_retention_days=int_value(
                "LENGRVIS_RUN_EVENT_RETENTION_DAYS",
                "run_event_retention_days",
                30,
                minimum=0,
            ),
            perception_enabled=flag("LENGRVIS_PERCEPTION_ENABLED", "enabled", False),
            perception_interval_seconds=float_value("LENGRVIS_PERCEPTION_INTERVAL_SECONDS", "interval_seconds", 5.0),
            perception_publish_events=flag("LENGRVIS_PERCEPTION_PUBLISH_EVENTS", "publish_events", True),
            perception_max_width=int_value("LENGRVIS_PERCEPTION_MAX_WIDTH", "max_width", 1280, minimum=1),
            perception_max_height=int_value("LENGRVIS_PERCEPTION_MAX_HEIGHT", "max_height", 720, minimum=1),
            perception_jpeg_quality=int_value("LENGRVIS_PERCEPTION_JPEG_QUALITY", "jpeg_quality", 70, minimum=1),
            perception_frame_diff_gate_enabled=flag(
                "LENGRVIS_PERCEPTION_FRAME_DIFF_GATE_ENABLED",
                "frame_diff_gate_enabled",
                True,
            ),
            perception_storage_enabled=flag("LENGRVIS_PERCEPTION_STORAGE_ENABLED", "storage_enabled", True),
            perception_store_screenshots=flag("LENGRVIS_PERCEPTION_STORE_SCREENSHOTS", "store_screenshots", False),
            perception_local_ocr_enabled=flag(
                "LENGRVIS_PERCEPTION_LOCAL_OCR_ENABLED",
                "local_ocr_enabled",
                False,
            ),
            perception_frame_diff_threshold=float_value(
                "LENGRVIS_PERCEPTION_FRAME_DIFF_THRESHOLD",
                "frame_diff_threshold",
                0.001,
            ),
            perception_sensitive_window_patterns=sensitive_window_items,
            perception_sensitive_field_names=sensitive_field_items,
            environment_app_context_interval_seconds=float_value(
                "LENGRVIS_ENVIRONMENT_APP_CONTEXT_INTERVAL_SECONDS",
                "app_context_interval_seconds",
                2.0,
            ),
            environment_store_screenshots=flag(
                "LENGRVIS_ENVIRONMENT_STORE_SCREENSHOTS", "environment_store_screenshots", False
            ),
            environment_event_retention_days=int_value(
                "LENGRVIS_ENVIRONMENT_EVENT_RETENTION_DAYS",
                "environment_event_retention_days",
                7,
                minimum=0,
            ),
            environment_rules=[dict(item) for item in environment_rules if isinstance(item, dict)],
            local_metrics_enabled=flag("LENGRVIS_LOCAL_METRICS_ENABLED", "local_metrics_enabled", False),
            jwt_secret=jwt_secret,
        )

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="python")
        data["api_key"] = "***" if self.api_key else ""
        data["jwt_secret"] = "***" if self.jwt_secret else ""
        data["mcp_servers"] = _redact_secret_fields(data.get("mcp_servers"))
        data = _redact_secret_fields(data)
        return data

    def merged(self, overrides: dict[str, Any] | None) -> AppSettings:
        if not overrides:
            return self
        data = self.model_dump(mode="python")
        for key, value in overrides.items():
            if hasattr(self, key) and value is not None:
                data[key] = value
        return AppSettings(**data)


def get_base_settings() -> AppSettings:
    settings = AppSettings.from_sources()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    return settings
