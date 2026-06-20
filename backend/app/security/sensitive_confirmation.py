from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.errors import AppError
from app.llm.registry import get_effective_settings
from app.policy.permissions import PermissionPolicy, PermissionRule, PermissionTimeWindow


CONFIRMATION_FIELD = "confirmation_nonce"
CONFIRMATION_TTL_SECONDS = 120
SENSITIVE_ENABLE_SETTINGS = {
    "allow_browser_network",
    "allow_cloud_context",
    "allow_file_content_upload",
    "allow_mock_fallback",
    "allow_unsafe_local_skill_execution",
    "developer_writes_enabled",
    "remote_desktop_enabled",
}
LLM_EGRESS_SETTINGS = {"base_url", "provider_name", "wire_api"}
PERMISSION_MODE_RELAXATION_ORDER = {
    "plan": 0,
    "default": 1,
    "trusted_edits": 2,
    "auto_review": 3,
    "dont_ask": 4,
}
_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sensitive_confirmations (
    nonce TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT
)
"""


def create_settings_confirmation(payload: dict[str, Any]) -> dict[str, Any]:
    changes = sensitive_settings_changes(payload)
    if not changes:
        return {"required": False, "nonce": "", "changes": []}
    return _create_confirmation("settings", changes)


def require_settings_confirmation(payload: dict[str, Any]) -> None:
    changes = sensitive_settings_changes(payload)
    if not changes:
        return
    _consume_confirmation(str(payload.get(CONFIRMATION_FIELD) or ""), "settings", changes)
    record("settings.sensitive_confirmed", "SettingsService", {"changes": changes})


def sensitive_settings_changes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    settings = get_effective_settings()
    changes: list[dict[str, Any]] = []
    for key in sorted(SENSITIVE_ENABLE_SETTINGS):
        if key not in payload:
            continue
        old_value = bool(getattr(settings, key, False))
        new_value = _truthy(payload.get(key))
        if not old_value and new_value:
            changes.append({"kind": "settings_enable", "key": key, "from": old_value, "to": new_value})
    if "requires_openai_auth" in payload:
        old_value = bool(getattr(settings, "requires_openai_auth", True))
        new_value = _truthy(payload.get("requires_openai_auth"))
        if old_value and not new_value:
            changes.append({"kind": "settings_disable_auth", "key": "requires_openai_auth", "from": old_value, "to": new_value})
    if "developer_writes_require_verification" in payload:
        old_value = bool(getattr(settings, "developer_writes_require_verification", True))
        new_value = _truthy(payload.get("developer_writes_require_verification"))
        if old_value and not new_value:
            changes.append(
                {
                    "kind": "settings_disable_developer_write_verification",
                    "key": "developer_writes_require_verification",
                    "from": old_value,
                    "to": new_value,
                }
            )
    if "strict_state_machine" in payload:
        old_value = bool(getattr(settings, "strict_state_machine", False))
        new_value = _truthy(payload.get("strict_state_machine"))
        if old_value and not new_value:
            changes.append(
                {
                    "kind": "settings_disable_strict_state_machine",
                    "key": "strict_state_machine",
                    "from": old_value,
                    "to": new_value,
                }
            )
    if "permission_mode" in payload:
        old_mode = _normalized_setting_value("permission_mode", getattr(settings, "permission_mode", "default"))
        new_mode = _normalized_setting_value("permission_mode", payload.get("permission_mode"))
        if _permission_mode_rank(new_mode) > _permission_mode_rank(old_mode):
            changes.append({"kind": "settings_permission_mode_relaxation", "key": "permission_mode", "from": old_mode, "to": new_mode})
    for key in sorted(LLM_EGRESS_SETTINGS):
        if key not in payload:
            continue
        old_value = _normalized_setting_value(key, getattr(settings, key, ""))
        new_value = _normalized_setting_value(key, payload.get(key))
        if old_value != new_value:
            changes.append({"kind": "settings_llm_egress_change", "key": key, "from": old_value, "to": new_value})
    if "mode" in payload:
        old_mode = _normalized_setting_value("mode", getattr(settings, "mode", ""))
        new_mode = _normalized_setting_value("mode", payload.get("mode"))
        if old_mode != new_mode and new_mode != "privacy":
            changes.append({"kind": "settings_llm_egress_change", "key": "mode", "from": old_mode, "to": new_mode})
    if "allowed_directories" in payload:
        additions = _added_values(getattr(settings, "allowed_directories", []) or [], payload.get("allowed_directories") or [])
        if additions:
            changes.append({"kind": "settings_expand_allowed_directories", "key": "allowed_directories", "added": additions})
    if "mcp_servers" in payload:
        additions = _enabled_mcp_additions(getattr(settings, "mcp_servers", []) or [], payload.get("mcp_servers") or [])
        if additions:
            changes.append({"kind": "settings_enable_mcp_servers", "key": "mcp_servers", "added": additions})
    return changes


def create_permission_policy_confirmation(changes: list[dict[str, Any]]) -> dict[str, Any]:
    if not changes:
        return {"required": False, "nonce": "", "changes": []}
    return _create_confirmation("permission_policy", changes)


def require_permission_policy_confirmation(changes: list[dict[str, Any]], nonce: str | None) -> None:
    if not changes:
        return
    _consume_confirmation(str(nonce or ""), "permission_policy", changes)
    record("permission_policy.relaxation_confirmed", "PermissionStore", {"changes": changes})


def permission_policy_relaxations(current: PermissionPolicy, next_policy: PermissionPolicy) -> list[dict[str, Any]]:
    current_rules = {rule.id: rule for rule in current.rules}
    changes: list[dict[str, Any]] = []
    for rule in next_policy.rules:
        previous = current_rules.pop(rule.id, None)
        relaxation = _rule_relaxation(previous, rule)
        if relaxation:
            changes.append(relaxation)
    for removed in current_rules.values():
        if _rule_is_deny(removed):
            changes.append(_rule_change("deny_rule_removed", removed))
    return changes


def permission_rule_relaxations(current_policy: PermissionPolicy, next_rule: PermissionRule) -> list[dict[str, Any]]:
    next_rules = [rule for rule in current_policy.rules if rule.id != next_rule.id]
    next_rules.append(next_rule)
    next_policy = PermissionPolicy(id=current_policy.id, rules=next_rules)
    return permission_policy_relaxations(current_policy, next_policy)


def permission_delete_relaxations(current_policy: PermissionPolicy, rule_id: str) -> list[dict[str, Any]]:
    next_policy = PermissionPolicy(
        id=current_policy.id,
        rules=[rule for rule in current_policy.rules if rule.id != rule_id],
    )
    return permission_policy_relaxations(current_policy, next_policy)


def _create_confirmation(scope: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
    _ensure_schema()
    now = _now()
    expires_at = now + timedelta(seconds=CONFIRMATION_TTL_SECONDS)
    nonce = secrets.token_urlsafe(24)
    fingerprint = _fingerprint(changes)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO sensitive_confirmations (nonce, scope, fingerprint, data, created_at, expires_at, used_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                nonce,
                scope,
                fingerprint,
                json.dumps({"changes": changes}, ensure_ascii=False, sort_keys=True),
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
    return {
        "required": True,
        "nonce": nonce,
        "expires_at": expires_at.isoformat(),
        "changes": changes,
    }


def _normalized_setting_value(key: str, value: Any) -> str:
    text = str(value or "").strip()
    if key == "permission_mode":
        aliases = {
            "accept_edits": "trusted_edits",
            "trusted": "trusted_edits",
            "auto": "auto_review",
            "dontask": "dont_ask",
            "deny": "dont_ask",
        }
        return aliases.get(text.lower(), text.lower())
    if key in {"mode", "provider_name", "wire_api"}:
        return text.lower()
    return text


def _permission_mode_rank(value: str) -> int:
    return PERMISSION_MODE_RELAXATION_ORDER.get(value, PERMISSION_MODE_RELAXATION_ORDER["default"])


def _consume_confirmation(nonce: str, scope: str, changes: list[dict[str, Any]]) -> None:
    if not nonce:
        raise AppError(
            "sensitive_confirmation_required",
            "This sensitive change requires a fresh confirmation.",
            status_code=409,
        )
    _ensure_schema()
    now = _now()
    fingerprint = _fingerprint(changes)
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM sensitive_confirmations WHERE expires_at < ? OR used_at IS NOT NULL",
            ((now - timedelta(seconds=CONFIRMATION_TTL_SECONDS)).isoformat(),),
        )
        row = conn.execute(
            """
            SELECT scope, fingerprint, expires_at, used_at
            FROM sensitive_confirmations
            WHERE nonce = ?
            """,
            (nonce,),
        ).fetchone()
        if not row:
            raise _invalid_confirmation()
        if row["scope"] != scope or row["fingerprint"] != fingerprint:
            raise _invalid_confirmation()
        if row["used_at"]:
            raise _invalid_confirmation()
        expires_at = datetime.fromisoformat(str(row["expires_at"]))
        if expires_at < now:
            raise AppError("sensitive_confirmation_expired", "The sensitive-change confirmation expired.", status_code=409)
        result = conn.execute(
            """
            UPDATE sensitive_confirmations
            SET used_at = ?
            WHERE nonce = ?
              AND used_at IS NULL
              AND scope = ?
              AND fingerprint = ?
              AND expires_at >= ?
            """,
            (now.isoformat(), nonce, scope, fingerprint, now.isoformat()),
        )
        if result.rowcount != 1:
            raise _invalid_confirmation()


def _ensure_schema() -> None:
    db.init_db()
    with db.connect() as conn:
        conn.execute(_TABLE_SQL)


def _fingerprint(changes: list[dict[str, Any]]) -> str:
    normalized = json.dumps(changes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _rule_is_allow(rule: PermissionRule) -> bool:
    return rule.enabled and rule.effect == "allow"


def _rule_is_deny(rule: PermissionRule) -> bool:
    return rule.enabled and rule.effect == "deny"


def _rule_relaxation(previous: PermissionRule | None, rule: PermissionRule) -> dict[str, Any] | None:
    if _rule_is_allow(rule):
        if previous is None or not _rule_is_allow(previous):
            return _rule_change("allow_rule_enabled", rule)
        widened = _widened_rule_fields(previous, rule)
        if widened:
            return _rule_change("allow_rule_scope_expanded", rule, widened)
    if previous is not None and _rule_is_deny(previous):
        if not _rule_is_deny(rule):
            return _rule_change("deny_rule_relaxed", previous)
        narrowed = _widened_rule_fields(rule, previous)
        if narrowed:
            return _rule_change("deny_rule_scope_narrowed", previous, narrowed)
    return None


def _widened_rule_fields(previous: PermissionRule, candidate: PermissionRule) -> list[str]:
    widened: list[str] = []
    if _patterns_are_wider(previous.tools, candidate.tools):
        widened.append("tools")
    if _patterns_are_wider(previous.path_patterns, candidate.path_patterns):
        widened.append("path_patterns")
    if _time_windows_are_wider(previous.time_windows, candidate.time_windows):
        widened.append("time_windows")
    return widened


def _patterns_are_wider(previous: list[str], candidate: list[str]) -> bool:
    previous_patterns = _normalized_patterns(previous)
    candidate_patterns = _normalized_patterns(candidate)
    if set(candidate_patterns) == set(previous_patterns):
        return False
    for pattern in candidate_patterns:
        if pattern in previous_patterns:
            continue
        if pattern in {"*", "**"}:
            return True
        if not any(_pattern_is_covered_by(pattern, previous_pattern) for previous_pattern in previous_patterns):
            return True
    return False


def _normalized_patterns(patterns: list[str]) -> list[str]:
    values = [str(pattern or "").replace("\\", "/").casefold().strip() for pattern in patterns if str(pattern or "").strip()]
    return values or ["*"]


def _pattern_is_covered_by(candidate: str, previous: str) -> bool:
    samples = _pattern_samples(candidate)
    return bool(samples) and all(fnmatchcase(sample, previous) for sample in samples)


def _pattern_samples(pattern: str) -> list[str]:
    literal = pattern.replace("**", "sample").replace("*", "sample").replace("?", "x")
    samples = [literal or "sample"]
    if "/" in literal:
        samples.append(literal.rsplit("/", 1)[-1])
    if "." not in literal:
        samples.append(f"{literal}.txt")
    return list(dict.fromkeys(samples))


def _time_windows_are_wider(previous: list[PermissionTimeWindow], candidate: list[PermissionTimeWindow]) -> bool:
    previous_windows = previous or [PermissionTimeWindow(days=list(range(7)), start="00:00", end="23:59")]
    candidate_windows = candidate or [PermissionTimeWindow(days=list(range(7)), start="00:00", end="23:59")]
    for window in candidate_windows:
        if not any(_time_window_covers(previous_window, window) for previous_window in previous_windows):
            return True
    return False


def _time_window_covers(previous: PermissionTimeWindow, candidate: PermissionTimeWindow) -> bool:
    if set(_window_days(candidate)) - set(_window_days(previous)):
        return False
    previous_start, previous_end = _window_minutes(previous)
    candidate_start, candidate_end = _window_minutes(candidate)
    return previous_start <= candidate_start and candidate_end <= previous_end


def _window_days(window: PermissionTimeWindow) -> list[int]:
    if not window.days:
        return list(range(7))
    result: list[int] = []
    for day in window.days:
        if day == "weekend":
            result.extend([5, 6])
        elif day == "weekday":
            result.extend([0, 1, 2, 3, 4])
        elif isinstance(day, int) and 0 <= day <= 6:
            result.append(day)
    return list(dict.fromkeys(result))


def _window_minutes(window: PermissionTimeWindow) -> tuple[int, int]:
    start = _clock_minutes(window.start)
    end = _clock_minutes(window.end)
    if start <= end:
        return start, end
    return 0, 23 * 60 + 59


def _clock_minutes(value: str) -> int:
    hour_text, _, minute_text = str(value or "00:00").partition(":")
    try:
        hour = int(hour_text)
        minute = int(minute_text or "0")
    except ValueError:
        hour, minute = 0, 0
    return max(0, min(hour, 23)) * 60 + max(0, min(minute, 59))


def _added_values(previous: list[Any], candidate: list[Any]) -> list[str]:
    previous_values = {_stable_text(value) for value in previous}
    added = [_stable_text(value) for value in candidate if _stable_text(value) not in previous_values]
    return [value for value in added if value]


def _enabled_mcp_additions(previous: list[dict[str, Any]], candidate: list[dict[str, Any]]) -> list[str]:
    previous_targets = {_mcp_target(server) for server in previous if _mcp_target(server)}
    additions: list[str] = []
    for server in candidate:
        if not isinstance(server, dict) or server.get("enabled") is False:
            continue
        target = _mcp_target(server)
        if target and target not in previous_targets:
            additions.append(target)
    return additions


def _mcp_target(server: dict[str, Any]) -> str:
    name = str(server.get("name") or server.get("id") or "").strip()
    target = str(server.get("url") or server.get("command") or "").strip()
    return _stable_text({"name": name, "target": target}) if target else ""


def _stable_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value or "").strip()


def _rule_change(kind: str, rule: PermissionRule, fields: list[str] | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "rule_id": rule.id,
        "effect": rule.effect,
        "tools": list(rule.tools),
        "path_patterns": list(rule.path_patterns),
        "fields": fields or [],
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _invalid_confirmation() -> AppError:
    return AppError("sensitive_confirmation_invalid", "The sensitive-change confirmation is invalid.", status_code=409)


def _now() -> datetime:
    return datetime.now(timezone.utc)
