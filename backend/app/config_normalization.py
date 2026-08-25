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
                allowed_tools = _normalize_string_list(
                    item.get("allowed_tools")
                    or item.get("allowedTools")
                    or item.get("approved_tools")
                    or item.get("approvedTools")
                )
                result.append(
                    {
                        "name": str(item.get("name") or item.get("id") or "mcp"),
                        "url": str(item.get("url") or ""),
                        "command": str(item.get("command") or ""),
                        "args": _normalize_string_list(item.get("args")),
                        "transport": str(item.get("transport", "http")),
                        "enabled": bool(item.get("enabled", True)),
                        "auth": dict(item.get("auth") or {}),
                        "env": _normalize_string_mapping(item.get("env")),
                        "inherit_env": _normalize_string_list(item.get("inherit_env") or item.get("inheritEnv")),
                        "owner": str(item.get("owner") or item.get("review_owner") or item.get("reviewOwner") or ""),
                        "policy_id": str(item.get("policy_id") or item.get("policyId") or ""),
                        "allowed_tools": allowed_tools,
                        "protocol_version": str(
                            item.get("protocol_version") or item.get("protocolVersion") or "2025-11-25"
                        ),
                        "strict_lifecycle": _normalize_bool(
                            item.get("strict_lifecycle", item.get("strictLifecycle", True)),
                            default=True,
                        ),
                        "client_name": str(item.get("client_name") or item.get("clientName") or "Lengrvis"),
                        "client_version": str(item.get("client_version") or item.get("clientVersion") or "0.1.2"),
                    }
                )
        return result
    return []


def _normalize_string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _normalize_string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key).strip(): str(item) for key, item in value.items() if str(key).strip()}


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


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
SECRET_VALUE_MAP_KEYS = {"env"}


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
            if key_text in SECRET_VALUE_MAP_KEYS and isinstance(item, dict):
                redacted[key] = {str(name): "***" for name in item}
            elif key_text in SECRET_CONTAINER_KEYS:
                redacted[key] = redact_secret_fields(item)
            else:
                redacted[key] = (
                    "***"
                    if item and any(token in key_text for token in SECRET_FIELD_TOKENS)
                    else redact_secret_fields(item)
                )
        return redacted
    return value
