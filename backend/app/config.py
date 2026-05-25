from __future__ import annotations

import os
import sys
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency guard
    yaml = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = PROJECT_ROOT / "backend"
DEFAULT_DATA_DIR = PROJECT_ROOT / ".marvis_data"
CONFIG_PARENT_SEARCH_DEPTH = 5
DEFAULT_JWT_SECRET = secrets.token_hex(32)


def _load_dotenv(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists() or yaml is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _candidate_config_dirs() -> list[Path]:
    roots: list[Path] = []
    for value in (
        os.environ.get("MARVIS_CONFIG_DIR"),
        os.environ.get("MAVRIS_CONFIG_DIR"),
        os.getcwd(),
        PROJECT_ROOT,
    ):
        if value:
            roots.append(Path(value))

    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)

    seen: set[str] = set()
    dirs: list[Path] = []
    for root in roots:
        try:
            current = root.resolve()
        except OSError:
            current = root
        for index, candidate in enumerate([current, *current.parents]):
            if index > CONFIG_PARENT_SEARCH_DEPTH:
                break
            key = str(candidate).lower()
            if key not in seen:
                seen.add(key)
                dirs.append(candidate)
    return dirs


def _find_config_file(file_name: str, explicit_env_key: str) -> Path | None:
    explicit = os.environ.get(explicit_env_key)
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None

    for directory in _candidate_config_dirs():
        path = directory / file_name
        if path.exists():
            return path
    return None


def _external_data_dir(config_file: Path | None, env_file: Path | None) -> Path:
    anchor = env_file or config_file
    if anchor:
        return anchor.parent / ".marvis_data"
    return DEFAULT_DATA_DIR


@dataclass(slots=True)
class AppSettings:
    provider_name: str = "mock"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    review_model: str = ""
    wire_api: str = "chat_completions"
    requires_openai_auth: bool = True
    model_reasoning_effort: str = "medium"
    disable_response_storage: bool = False
    network_access: str = "disabled"
    model_context_window: int = 128000
    model_auto_compact_token_limit: int = 96000
    embedding_model: str = "text-embedding-3-small"
    vision_model: str = ""
    onnx_model_path: str = ""
    onnx_execution_provider: str = ""
    temperature: float = 0.2
    max_tokens: int = 1600
    timeout: int = 30
    mode: str = "privacy"
    allow_cloud_context: bool = False
    allow_file_content_upload: bool = False
    allow_browser_network: bool = False
    remote_desktop_enabled: bool = False
    app_allowlist: list[str] = field(default_factory=list)
    browser_max_page_bytes: int = 250000
    document_max_chars_to_llm: int = 30000
    browser_screenshot_dir: str = str(DEFAULT_DATA_DIR / "browser_screenshots")
    allowed_directories: list[str] = field(default_factory=list)
    data_dir: str = str(DEFAULT_DATA_DIR)
    skill_directories: list[str] = field(default_factory=list)
    mcp_servers: list[dict] = field(default_factory=list)
    allow_mock_fallback: bool = True
    strict_state_machine: bool = False
    jwt_secret: str = field(default_factory=lambda: DEFAULT_JWT_SECRET)

    @classmethod
    def from_sources(cls) -> "AppSettings":
        config_path = _find_config_file("config.yaml", "MARVIS_CONFIG_FILE")
        env_path = _find_config_file(".env", "MARVIS_ENV_FILE")
        config = _load_yaml(config_path) if config_path else {}
        env_file = _load_dotenv(env_path) if env_path else {}
        env = {**env_file, **os.environ}
        default_data_dir = _external_data_dir(config_path, env_path)

        llm = config.get("llm", {}) if isinstance(config.get("llm"), dict) else {}
        privacy = config.get("privacy", {}) if isinstance(config.get("privacy"), dict) else {}
        paths = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
        orchestration = config.get("orchestration", {}) if isinstance(config.get("orchestration"), dict) else {}

        def value(env_key: str, yaml_key: str, default: Any) -> Any:
            return (
                env.get(env_key)
                or llm.get(yaml_key)
                or privacy.get(yaml_key)
                or paths.get(yaml_key)
                or orchestration.get(yaml_key)
                or default
            )

        def value_any(env_keys: tuple[str, ...], yaml_key: str, default: Any) -> Any:
            for env_key in env_keys:
                raw = env.get(env_key)
                if raw:
                    return raw
            return (
                llm.get(yaml_key)
                or privacy.get(yaml_key)
                or paths.get(yaml_key)
                or orchestration.get(yaml_key)
                or default
            )

        def flag(env_key: str, yaml_key: str, default: bool) -> bool:
            raw = value(env_key, yaml_key, str(default).lower())
            if isinstance(raw, bool):
                return raw
            return str(raw).lower() in {"1", "true", "yes", "on"}

        allowed = value("MARVIS_ALLOWED_DIRECTORIES", "allowed_directories", [])
        if isinstance(allowed, str):
            allowed_dirs = [p.strip() for p in allowed.split(";") if p.strip()]
        elif isinstance(allowed, list):
            allowed_dirs = [str(p) for p in allowed]
        else:
            allowed_dirs = []

        skill_directories = value("MARVIS_SKILL_DIRECTORIES", "skill_directories", [])
        if isinstance(skill_directories, str):
            skill_dirs = [p.strip() for p in skill_directories.split(";") if p.strip()]
        elif isinstance(skill_directories, list):
            skill_dirs = [str(p) for p in skill_directories if str(p).strip()]
        else:
            skill_dirs = []

        app_allowlist = value("MARVIS_APP_ALLOWLIST", "app_allowlist", ["notepad", "calculator", "calc"])
        if isinstance(app_allowlist, str):
            app_allowlist_items = [item.strip().lower() for item in app_allowlist.split(";") if item.strip()]
        elif isinstance(app_allowlist, list):
            app_allowlist_items = [str(item).strip().lower() for item in app_allowlist if str(item).strip()]
        else:
            app_allowlist_items = ["notepad", "calculator", "calc"]

        return cls(
            provider_name=str(value("MARVIS_PROVIDER_NAME", "provider_name", "mock")),
            base_url=str(value("MARVIS_BASE_URL", "base_url", "https://api.openai.com/v1")),
            api_key=str(value("MARVIS_API_KEY", "api_key", "")),
            model=str(value("MARVIS_MODEL", "model", "gpt-4o-mini")),
            review_model=str(value("MARVIS_REVIEW_MODEL", "review_model", "")),
            wire_api=str(value("MARVIS_WIRE_API", "wire_api", "chat_completions")),
            requires_openai_auth=flag("MARVIS_REQUIRES_OPENAI_AUTH", "requires_openai_auth", True),
            model_reasoning_effort=str(value("MARVIS_MODEL_REASONING_EFFORT", "model_reasoning_effort", "medium")),
            disable_response_storage=flag("MARVIS_DISABLE_RESPONSE_STORAGE", "disable_response_storage", False),
            network_access=str(value("MARVIS_NETWORK_ACCESS", "network_access", "disabled")),
            model_context_window=int(value("MARVIS_MODEL_CONTEXT_WINDOW", "model_context_window", 128000)),
            model_auto_compact_token_limit=int(
                value("MARVIS_MODEL_AUTO_COMPACT_TOKEN_LIMIT", "model_auto_compact_token_limit", 96000)
            ),
            embedding_model=str(value("MARVIS_EMBEDDING_MODEL", "embedding_model", "text-embedding-3-small")),
            vision_model=str(value("MARVIS_VISION_MODEL", "vision_model", "")),
            onnx_model_path=str(
                value_any(("MARVIS_ONNX_MODEL_PATH", "MAVRIS_ONNX_MODEL_PATH"), "onnx_model_path", "")
            ),
            onnx_execution_provider=str(
                value_any(
                    ("MARVIS_ONNX_EXECUTION_PROVIDER", "MAVRIS_ONNX_EXECUTION_PROVIDER"),
                    "onnx_execution_provider",
                    "",
                )
            ),
            temperature=float(value("MARVIS_TEMPERATURE", "temperature", 0.2)),
            max_tokens=int(value("MARVIS_MAX_TOKENS", "max_tokens", 1600)),
            timeout=int(value("MARVIS_TIMEOUT", "timeout", 30)),
            mode=str(value("MARVIS_MODE", "mode", "privacy")),
            allow_cloud_context=flag("MARVIS_ALLOW_CLOUD_CONTEXT", "allow_cloud_context", False),
            allow_file_content_upload=flag("MARVIS_ALLOW_FILE_CONTENT_UPLOAD", "allow_file_content_upload", False),
            allow_browser_network=flag("MARVIS_ALLOW_BROWSER_NETWORK", "allow_browser_network", False),
            remote_desktop_enabled=flag("MARVIS_REMOTE_DESKTOP_ENABLED", "remote_desktop_enabled", False),
            app_allowlist=app_allowlist_items,
            browser_max_page_bytes=int(value("MARVIS_BROWSER_MAX_PAGE_BYTES", "browser_max_page_bytes", 250000)),
            document_max_chars_to_llm=int(value("MARVIS_DOCUMENT_MAX_CHARS_TO_LLM", "document_max_chars_to_llm", 30000)),
            browser_screenshot_dir=str(
                value("MARVIS_BROWSER_SCREENSHOT_DIR", "browser_screenshot_dir", default_data_dir / "browser_screenshots")
            ),
            allowed_directories=allowed_dirs,
            data_dir=str(value("MARVIS_DATA_DIR", "data_dir", default_data_dir)),
            skill_directories=skill_dirs,
            mcp_servers=_normalize_mcp_servers(value("MARVIS_MCP_SERVERS", "mcp_servers", [])),
            allow_mock_fallback=flag("MARVIS_ALLOW_MOCK_FALLBACK", "allow_mock_fallback", True),
            strict_state_machine=flag("MARVIS_STRICT_STATE_MACHINE", "strict_state_machine", False),
            jwt_secret=str(
                value_any(("MARVIS_JWT_SECRET", "MAVRIS_JWT_SECRET"), "jwt_secret", DEFAULT_JWT_SECRET)
            ),
        )

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key"] = "***" if self.api_key else ""
        data["jwt_secret"] = "***" if self.jwt_secret else ""
        return data

    def merged(self, overrides: dict[str, Any] | None) -> "AppSettings":
        if not overrides:
            return self
        data = asdict(self)
        for key, value in overrides.items():
            if hasattr(self, key) and value is not None:
                data[key] = value
        return AppSettings(**data)


def get_base_settings() -> AppSettings:
    settings = AppSettings.from_sources()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    return settings


def _normalize_mcp_servers(value: Any) -> list[dict]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        import json as _json

        try:
            parsed = _json.loads(value)
        except (ValueError, TypeError):
            return []
        return _normalize_mcp_servers(parsed)
    if isinstance(value, list):
        result: list[dict] = []
        for item in value:
            if isinstance(item, dict) and item.get("url"):
                result.append(
                    {
                        "name": str(item.get("name") or item.get("id") or "mcp"),
                        "url": str(item["url"]),
                        "transport": str(item.get("transport", "http")),
                        "enabled": bool(item.get("enabled", True)),
                    }
                )
        return result
    return []
