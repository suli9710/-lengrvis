from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from app.config import AppSettings
from app.policy.redaction import redact_value


APPROVAL_HMAC_ENV_KEYS = ("MARVIS_APPROVAL_HMAC_SECRET", "MAVRIS_APPROVAL_HMAC_SECRET")
DEFAULT_APPROVAL_HMAC_SECRET = "marvis-local-approval-binding-secret"


def approval_secret() -> str:
    for key in APPROVAL_HMAC_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            return value
    return DEFAULT_APPROVAL_HMAC_SECRET


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def hmac_digest(value: Any, *, prefix: str = "hmac") -> str:
    body = canonical_json(value).encode("utf-8")
    digest = hmac.new(approval_secret().encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{prefix}:{digest}"


def short_digest(value: str, *, length: int = 12) -> str:
    return value.split(":", 1)[-1][:length]


def redacted_preview(preview: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_value(preview)
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


def settings_fingerprint(settings: AppSettings | None, *, allowed_directories: list[str] | None = None) -> str:
    payload = {
        "mode": getattr(settings, "mode", ""),
        "allow_cloud_context": bool(getattr(settings, "allow_cloud_context", False)),
        "allow_browser_network": bool(getattr(settings, "allow_browser_network", False)),
        "allow_file_content_upload": bool(getattr(settings, "allow_file_content_upload", False)),
        "allowed_directories": sorted(str(path) for path in (allowed_directories or getattr(settings, "allowed_directories", []) or [])),
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
