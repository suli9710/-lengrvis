from __future__ import annotations

import json
from typing import Any


def normalize_permission_mode(value: Any) -> str:
    candidate = str(value or "default").strip().lower()
    aliases = {
        "accept_edits": "trusted_edits",
        "trusted": "trusted_edits",
        "auto": "auto_review",
        "dontask": "dont_ask",
        "deny": "dont_ask",
    }
    candidate = aliases.get(candidate, candidate)
    if candidate not in {"plan", "default", "trusted_edits", "auto_review", "dont_ask"}:
        return "default"
    return candidate


def normalize_mcp_servers(value: Any) -> list[dict]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return []
        return normalize_mcp_servers(parsed)
    if isinstance(value, list):
        result: list[dict] = []
        for item in value:
            if isinstance(item, dict) and (item.get("url") or item.get("command")):
                result.append(
                    {
                        "name": str(item.get("name") or item.get("id") or "mcp"),
                        "url": str(item.get("url") or ""),
                        "command": str(item.get("command") or ""),
                        "args": list(item.get("args") or []),
                        "transport": str(item.get("transport", "http")),
                        "enabled": bool(item.get("enabled", True)),
                        "auth": dict(item.get("auth") or {}),
                    }
                )
        return result
    return []


def normalize_skill_trusted_public_keys(value: Any) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            result: dict[str, str] = {}
            for entry in text.split(";"):
                if "=" not in entry:
                    continue
                key_id, public_key = entry.split("=", 1)
                key_id = key_id.strip()
                public_key = public_key.strip()
                if key_id and public_key:
                    result[key_id] = public_key
            return result
        return normalize_skill_trusted_public_keys(parsed)
    if isinstance(value, dict):
        return {
            str(key).strip(): str(item).strip() for key, item in value.items() if str(key).strip() and str(item).strip()
        }
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            key_id = str(item.get("key_id") or item.get("keyId") or item.get("id") or "").strip()
            public_key = str(item.get("public_key") or item.get("publicKey") or item.get("key") or "").strip()
            if key_id and public_key:
                result[key_id] = public_key
        return result
    return {}


SECRET_FIELD_TOKENS = ("auth", "authorization", "api_key", "token", "password", "secret", "credential")
SECRET_CONTAINER_KEYS = {"headers"}


def redact_secret_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [redact_secret_fields(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secret_fields(item) for item in value]
    if isinstance(value, set):
        return [redact_secret_fields(item) for item in sorted(value, key=str)]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).replace("-", "_").casefold()
            if key_text in SECRET_CONTAINER_KEYS:
                redacted[key] = redact_secret_fields(item)
            else:
                redacted[key] = (
                    "***"
                    if item and any(token in key_text for token in SECRET_FIELD_TOKENS)
                    else redact_secret_fields(item)
                )
        return redacted
    return value
