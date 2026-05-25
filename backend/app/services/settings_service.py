from __future__ import annotations

from dataclasses import fields
from typing import Any

from app.core import db
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.prompts import load_prompt
from app.llm.registry import get_effective_settings, get_provider


def get_settings() -> dict[str, Any]:
    return get_effective_settings().public_dict()


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {field.name for field in fields(get_effective_settings())}
    for key, value in payload.items():
        if key in allowed:
            db.set_setting(key, _coerce_setting_value(key, value))
    return get_settings()


def _coerce_setting_value(key: str, value: Any) -> Any:
    if key in {"allowed_directories", "app_allowlist", "skill_directories"}:
        if isinstance(value, str):
            return [item.strip() for item in value.replace("\n", ";").split(";") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []
    if key in {"browser_max_page_bytes", "document_max_chars_to_llm"}:
        return max(1, int(value))
    if key in {"onnx_model_path", "onnx_execution_provider", "jwt_secret"}:
        return str(value or "").strip()
    if key == "mode":
        candidate = str(value).strip().lower()
        if candidate not in {"privacy", "efficiency", "hybrid"}:
            return "privacy"
        return candidate
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
    }:
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}
    return value


async def test_llm_provider() -> dict[str, Any]:
    try:
        provider = get_provider()
        text = await provider.chat([{"role": "user", "content": load_prompt("settings_test_llm_provider.md")}])
        return {"ok": True, "provider": provider.name, "message": text}
    except LocalBackendUnavailable as exc:
        return {"ok": False, "provider": "local", "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "provider": provider.name, "error": str(exc)}
