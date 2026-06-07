from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from pathlib import Path
from typing import Any

from app.config import AppSettings, _find_config_file, _load_dotenv, env_value, get_base_settings, get_env
from app.policy.redaction import redact_value


APPROVAL_HMAC_ENV_KEYS = ("LENGRVIS_APPROVAL_HMAC_SECRET",)
APPROVAL_HMAC_SECRET_FILE = "approval_hmac.secret"
logger = logging.getLogger(__name__)


def approval_secret() -> str:
    for key in APPROVAL_HMAC_ENV_KEYS:
        value = get_env(key)
        if value:
            return value
    env_path = _find_config_file(".env", "LENGRVIS_ENV_FILE")
    dotenv_values = _load_dotenv(env_path) if env_path else {}
    for key in APPROVAL_HMAC_ENV_KEYS:
        value = env_value(dotenv_values, key)
        if value:
            return value
    return _local_approval_secret()


def _local_approval_secret() -> str:
    data_dir = Path(get_base_settings().data_dir)
    secret_path = data_dir / APPROVAL_HMAC_SECRET_FILE
    try:
        if secret_path.exists():
            value = secret_path.read_text(encoding="utf-8").strip()
            if value:
                return value
        data_dir.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        secret_path.write_text(value, encoding="utf-8")
        try:
            secret_path.chmod(0o600)
        except OSError as exc:
            logger.debug("could not restrict approval HMAC secret permissions at %s: %s", secret_path, exc)
        return value
    except OSError as exc:
        raise RuntimeError("Approval HMAC secret is unavailable.") from exc


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def hmac_digest(value: Any, *, prefix: str = "hmac") -> str:
    body = canonical_json(value).encode("utf-8")
    digest = hmac.new(approval_secret().encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{prefix}:{digest}"


def short_digest(value: str, *, length: int = 12) -> str:
    return value.split(":", 1)[-1][:length]


def redacted_preview(preview: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_value(_public_preview(preview))
    return redacted if isinstance(redacted, dict) else {"preview": redacted}


def binding_preview(preview: dict[str, Any]) -> dict[str, Any]:
    redacted = _binding_redact(preview)
    return redacted if isinstance(redacted, dict) else {"preview": redacted}


def canonical_args(args: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _jsonable(value)
        for key, value in args.items()
        if key not in {"dry_run", "approved", "approval_id"}
    }


def args_binding_hmac(tool_name: str, args: dict[str, Any], *, task_id: str = "", step_id: str | None = None) -> str:
    return hmac_digest(
        {
            "task_id": task_id,
            "step_id": step_id or "",
            "tool_name": tool_name,
            "args": canonical_args(args),
        },
        prefix="args",
    )


def preview_hmac(preview: dict[str, Any]) -> str:
    return hmac_digest(preview, prefix="preview")


def _public_preview(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _public_preview(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, list):
        return [_public_preview(item) for item in value]
    if isinstance(value, tuple):
        return [_public_preview(item) for item in value]
    return value


def _binding_redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            if text_key.startswith("_"):
                result[text_key] = _jsonable(item)
            elif _is_preview_path_key(text_key):
                result[text_key] = _jsonable(item)
            else:
                result[text_key] = _binding_redact(item)
        return result
    if isinstance(value, list):
        return [_binding_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_binding_redact(item) for item in value]
    return redact_value(value)


def _is_preview_path_key(key: str) -> bool:
    normalized = key.replace("-", "_").casefold()
    return normalized in {"path", "from", "to", "source", "destination"} or normalized.endswith("_path")


def settings_fingerprint(settings: AppSettings | None, *, allowed_directories: list[str] | None = None) -> str:
    directory_values = getattr(settings, "allowed_directories", []) if allowed_directories is None else allowed_directories
    payload = {
        "mode": getattr(settings, "mode", ""),
        "permission_mode": getattr(settings, "permission_mode", ""),
        "network_access": getattr(settings, "network_access", ""),
        "allow_cloud_context": bool(getattr(settings, "allow_cloud_context", False)),
        "allow_browser_network": bool(getattr(settings, "allow_browser_network", False)),
        "allow_file_content_upload": bool(getattr(settings, "allow_file_content_upload", False)),
        "allow_unsafe_local_skill_execution": bool(getattr(settings, "allow_unsafe_local_skill_execution", False)),
        "remote_desktop_enabled": bool(getattr(settings, "remote_desktop_enabled", False)),
        "lan_tls_enabled": bool(getattr(settings, "lan_tls_enabled", False)),
        "lan_public_base_url": getattr(settings, "lan_public_base_url", ""),
        "app_allowlist": sorted(str(item).casefold() for item in getattr(settings, "app_allowlist", []) or []),
        "browser_max_page_bytes": getattr(settings, "browser_max_page_bytes", 0),
        "allowed_directories": sorted(str(path) for path in (directory_values or [])),
    }
    return hmac_digest(payload, prefix="settings")


def permission_policy_version(policy_updated_at: str = "") -> str:
    return hmac_digest({"permission_policy_updated_at": policy_updated_at or ""}, prefix="perm")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return [_jsonable(item) for item in sorted(value, key=str)]
    return value
