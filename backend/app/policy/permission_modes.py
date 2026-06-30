from __future__ import annotations

from typing import Any

from app.policy.risk import RiskLevel

PERMISSION_MODES = {"plan", "default", "trusted_edits", "auto_review", "dont_ask"}
TRUSTED_AUTO_EDIT_TIERS = {"builtin", "core", "first_party"}
AUTO_EDIT_FORBIDDEN_EFFECTS = {
    "delete",
    "payment",
    "send",
    "submit",
    "external_post",
    "credential",
    "execute_local_code",
    "execute_test",
    "system",
    "browser_write",
}


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
    return candidate if candidate in PERMISSION_MODES else "default"


def permission_mode_from_context(context: dict[str, Any] | None = None, settings: Any | None = None) -> str:
    context = context or {}
    raw = context.get("permission_mode")
    if raw is None:
        raw_settings = context.get("settings") or settings
        raw = getattr(raw_settings, "permission_mode", None)
    return normalize_permission_mode(raw)


def is_modifying_risk(risk: RiskLevel) -> bool:
    return risk in {
        RiskLevel.R2_REVERSIBLE_MODIFY,
        RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
    }


def trusted_reversible_edit_allowed(tool_definition: Any | None, args: dict[str, Any] | None = None) -> bool:
    if tool_definition is None:
        return False
    risk = getattr(tool_definition, "risk_level", None)
    if risk != RiskLevel.R2_REVERSIBLE_MODIFY:
        return False
    if not bool(getattr(tool_definition, "supports_dry_run", False)):
        return False
    if bool(getattr(tool_definition, "destructive", False)):
        return False
    trust_tier = str(getattr(tool_definition, "trust_tier", "unknown") or "unknown").casefold()
    if trust_tier not in TRUSTED_AUTO_EDIT_TIERS:
        return False
    effects = {str(item).casefold() for item in (getattr(tool_definition, "effects", None) or [])}
    if not effects or effects & AUTO_EDIT_FORBIDDEN_EFFECTS:
        return False
    if _contains_runtime_or_sensitive_path(args or {}):
        return False
    return True


def _contains_runtime_or_sensitive_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_runtime_or_sensitive_path(child) for child in value.values())
    if isinstance(value, list | tuple | set):
        return any(_contains_runtime_or_sensitive_path(child) for child in value)
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/").casefold()
    return any(
        normalized.startswith(prefix)
        for prefix in (
            "c:/windows",
            "c:/program files",
            "c:/program files (x86)",
            "c:/programdata",
            "/etc",
            "/bin",
            "/sbin",
            "/usr",
            "/var",
            "/system",
            "/library",
        )
    )
