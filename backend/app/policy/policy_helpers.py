from __future__ import annotations

from pathlib import PureWindowsPath
from typing import Any

from app.policy.policy_rules import PATH_ARG_KEYS, SYSTEM_PATH_PREFIXES
from app.policy.risk import RiskLevel


def contains_sensitive_arg(value: Any, sensitive_keys: set[str]) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).casefold()
            if any(term in normalized for term in sensitive_keys):
                return True
            if contains_sensitive_arg(item, sensitive_keys):
                return True
        return False
    if isinstance(value, list | tuple | set):
        return any(contains_sensitive_arg(item, sensitive_keys) for item in value)
    if isinstance(value, str):
        text = value.casefold()
        return any(
            term in text
            for term in {"password", "token", "cookie", "credential", "private key", "payment", "otp", "2fa"}
        )
    return False


def ui_selector_text(args: dict[str, Any]) -> str:
    selector = args.get("selector")
    parts: list[Any] = []
    if isinstance(selector, dict):
        parts.extend(selector.values())
    elif selector:
        parts.append(selector)
    for key in (
        "name",
        "name_contains",
        "nameContains",
        "text_contains",
        "textContains",
        "automation_id",
        "automationId",
        "class_name",
        "className",
        "control_type",
        "controlType",
    ):
        if key in args:
            parts.append(args.get(key))
    return " ".join(str(part) for part in parts if part is not None).casefold()


def contains_system_path(args: dict[str, Any]) -> bool:
    return any(is_system_path(path) for path in candidate_paths(args))


def cleanup_args_touch_system_or_sensitive_path(args: dict[str, Any]) -> bool:
    sensitive_terms = {
        ".ssh",
        "api_key",
        "apikey",
        "cookie",
        "credential",
        "credentials",
        "id_rsa",
        "key",
        "password",
        "passwd",
        "private_key",
        "secret",
        "token",
    }
    for path in candidate_paths(args):
        normalized = normalized_path(path)
        if is_system_path(path) or any(term in normalized for term in sensitive_terms):
            return True
    return False


def cleanup_has_trash_with_prompt(args: dict[str, Any]) -> bool:
    if str(args.get("action") or "").casefold() == "trash_with_prompt":
        return True
    for item in args.get("items") or args.get("selected_items") or []:
        if isinstance(item, dict) and str(item.get("action") or "").casefold() == "trash_with_prompt":
            return True
    return False


def candidate_paths(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).casefold()
            if normalized_key in PATH_ARG_KEYS or "path" in normalized_key:
                result.extend(candidate_paths(item))
            elif isinstance(item, dict | list | tuple | set):
                result.extend(candidate_paths(item))
        return result
    if isinstance(value, list | tuple | set):
        for item in value:
            result.extend(candidate_paths(item))
        return result
    if isinstance(value, str):
        text = value.strip()
        if text:
            result.append(text)
    return result


def is_system_path(path: str) -> bool:
    normalized = normalized_path(path)
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in SYSTEM_PATH_PREFIXES)


def normalized_path(path: str) -> str:
    text = path.strip().replace("\\", "/")
    if not text:
        return ""
    try:
        pure = PureWindowsPath(text)
        if pure.drive:
            text = pure.as_posix()
    except (TypeError, ValueError):
        return text.rstrip("/").casefold()
    return text.rstrip("/").casefold()


def browser_activity_risk(args: dict[str, Any] | None) -> RiskLevel:
    payload = args or {}
    kind = str(payload.get("kind") or "").strip().casefold().replace("_", "-")
    action = payload.get("action")
    if not kind and isinstance(action, dict):
        kind = str(action.get("kind") or "").strip().casefold().replace("_", "-")
    if kind in {"open", "navigate"}:
        return RiskLevel.R1_OPEN_ONLY
    if kind in {"observe", "screenshot", "wait"}:
        return RiskLevel.R0_READ_ONLY
    if kind in {"click", "fill", "scroll"}:
        return RiskLevel.R2_REVERSIBLE_MODIFY
    if kind in {"submit", "cua", "computer-use"}:
        return RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
    return RiskLevel.R2_REVERSIBLE_MODIFY
