from __future__ import annotations

import fnmatch
import json
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field, field_validator, model_validator

from app.core import db
from app.core.schemas import now_iso
from app.policy.policy_helpers import candidate_paths, canonicalize_path
from app.security.capability_manifest import assert_capability_allowed, permission_policy_capability_payload

PermissionEffect = Literal["allow", "deny"]
BUILTIN_BASELINE_DENY_RULE_ID = "builtin_high_risk_baseline"
BUILTIN_BASELINE_DENY_RULE_NAME = "Built-in high-risk baseline"
BUILTIN_AMBIGUOUS_PATH_RULE_ID = "builtin_ambiguous_path"
BUILTIN_AMBIGUOUS_PATH_RULE_NAME = "Ambiguous path rejected"
_BUILTIN_BASELINE_DENY_PATTERNS: tuple[str, ...] = (
    "mcp.*",
    "external.*",
    "*.click*",
    "*.copy",
    "*.create*",
    "*.delete*",
    "*.drag*",
    "*.edit*",
    "*.fill*",
    "*.generate*",
    "*.hotkey*",
    "*.key_press",
    "*.launch*",
    "*.move",
    "*.rename",
    "*.submit*",
    "*.trash*",
    "*.type*",
    "*.uninstall*",
    "*.write*",
    "*.cleanup_execute",
    "*.cleanup_rollback",
    "app.excel.*",
    "shell.*",
    "*.shell*",
    "dev.test_run",
    "remote.click",
    "remote.key_press",
    "remote.type_text",
    "ui_automation.click",
    "ui_automation.click_at",
    "ui_automation.drag",
    "ui_automation.focus",
    "ui_automation.focus_window",
    "ui_automation.hotkey",
    "ui_automation.key_press",
    "ui_automation.type_text",
    "workflow.run",
    "browser.act",
    "browser.cua",
    "browser.cua_run",
    "browser.click_element",
    "browser.fill_form",
    "browser.submit_form",
)


class PermissionTimeWindow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    start: str = "00:00"
    end: str = "23:59"
    days: list[int | str] = Field(default_factory=list)
    timezone: str = ""

    @field_validator("start", "end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        hour, minute = _parse_clock(value)
        return f"{hour:02d}:{minute:02d}"

    @field_validator("days")
    @classmethod
    def validate_days(cls, value: list[int | str]) -> list[int | str]:
        result: list[int | str] = []
        for day in value:
            normalized = _normalize_day(day)
            if normalized is not None:
                result.append(normalized)
        return result


class PermissionRule(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: f"perm_{uuid4().hex}")
    name: str = ""
    effect: PermissionEffect = "deny"
    tools: list[str] = Field(default_factory=list)
    path_patterns: list[str] = Field(default_factory=list)
    time_windows: list[PermissionTimeWindow] = Field(default_factory=list)
    enabled: bool = True
    reason: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @model_validator(mode="before")
    @classmethod
    def accept_single_rule_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        tool = normalized.pop("tool", None)
        if tool is not None and not normalized.get("tools"):
            normalized["tools"] = [tool]
        path_pattern = normalized.pop("path_pattern", None)
        if path_pattern is None:
            path_pattern = normalized.pop("pathPattern", None)
        if path_pattern is not None and not normalized.get("path_patterns"):
            normalized["path_patterns"] = [path_pattern]
        time_window = normalized.pop("time_window", None)
        if time_window is None:
            time_window = normalized.pop("timeWindow", None)
        if time_window is not None and not normalized.get("time_windows"):
            normalized["time_windows"] = [time_window] if isinstance(time_window, dict) else []
        return normalized

    @field_validator("tools", "path_patterns")
    @classmethod
    def normalize_patterns(cls, value: list[str]) -> list[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @computed_field
    @property
    def tool(self) -> str:
        return self.tools[0] if self.tools else "*"

    @computed_field
    @property
    def path_pattern(self) -> str:
        return self.path_patterns[0] if self.path_patterns else "*"

    @computed_field
    @property
    def time_window(self) -> PermissionTimeWindow | None:
        return self.time_windows[0] if self.time_windows else None


class PermissionPolicy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = "default"
    rules: list[PermissionRule] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)

    def evaluate(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> PermissionDecision:
        return evaluate_permission_policy(self, tool_name=tool_name, args=args, context=context, now=now)


class PermissionDecision(BaseModel):
    allowed: bool
    matched: bool = False
    effect: PermissionEffect | None = None
    rule_id: str = ""
    rule_name: str = ""
    reason: str = "No permission rule matched."

    @property
    def matched_rule_id(self) -> str:
        return self.rule_id


def is_builtin_baseline_deny(decision: Any) -> bool:
    return (
        str(getattr(decision, "rule_id", "") or "") == BUILTIN_BASELINE_DENY_RULE_ID
        and str(getattr(decision, "effect", "") or "") == "deny"
    )


class PermissionStore:
    def __init__(self, policy_id: str = "default") -> None:
        self.policy_id = policy_id

    def get_policy(self) -> PermissionPolicy:
        self._ensure_schema()
        with db.connect() as conn:
            try:
                row = conn.execute("SELECT data FROM permission_policies WHERE id = ?", (self.policy_id,)).fetchone()
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: sensitive schema failures fail closed.
                raise db.SensitiveRecordIntegrityError("Sensitive permission policy table is unavailable") from exc
            proof_exists = db._sensitive_record_integrity_row_exists(conn, "permission_policies", self.policy_id)
            presence_exists = _permission_policy_presence_exists(conn, self.policy_id)
            anchor_complete = _sensitive_integrity_anchor_complete(conn)
        if not row:
            if proof_exists or presence_exists or (anchor_complete and self.policy_id == "default"):
                raise db.SensitiveRecordIntegrityError(
                    f"Sensitive permission policy record is missing for {self.policy_id}"
                )
            return PermissionPolicy(id=self.policy_id)
        db.require_sensitive_record_integrity("permission_policies", self.policy_id, str(row["data"]))
        return _parse_stored_permission_policy(str(row["data"]), policy_id=self.policy_id)

    def updated_at(self) -> str:
        self._ensure_schema()
        with db.connect() as conn:
            row = conn.execute("SELECT updated_at FROM permission_policies WHERE id = ?", (self.policy_id,)).fetchone()
            if row is None and (
                db._sensitive_record_integrity_row_exists(conn, "permission_policies", self.policy_id)
                or _permission_policy_presence_exists(conn, self.policy_id)
                or (_sensitive_integrity_anchor_complete(conn) and self.policy_id == "default")
            ):
                raise db.SensitiveRecordIntegrityError(
                    f"Sensitive permission policy record is missing for {self.policy_id}"
                )
        return str(row["updated_at"]) if row else ""

    def save_policy(self, policy: PermissionPolicy | dict[str, Any]) -> PermissionPolicy:
        self._ensure_schema()
        model = PermissionPolicy.model_validate(policy)
        model.id = self.policy_id
        model.updated_at = now_iso()
        serialized = model.model_dump_json()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO permission_policies (id, data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
                """,
                (self.policy_id, serialized, model.updated_at),
            )
            db.store_sensitive_record_integrity("permission_policies", self.policy_id, serialized, conn=conn)
        return model

    def add_rule(self, rule: PermissionRule | dict[str, Any]) -> PermissionPolicy:
        # P1-4 fix: perform the read-modify-write inside a single BEGIN IMMEDIATE
        # transaction so concurrent add_rule/upsert_rule calls cannot clobber each
        # other (lost update). The previous implementation read via get_policy()
        # and wrote via save_policy() across two separate connections, leaving a
        # race window where a concurrent writer's rule could be silently dropped.
        model = PermissionRule.model_validate(rule)
        self._ensure_schema()
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT data FROM permission_policies WHERE id = ?", (self.policy_id,)).fetchone()
            if row:
                db.require_sensitive_record_integrity("permission_policies", self.policy_id, str(row["data"]))
                policy = _parse_stored_permission_policy(str(row["data"]), policy_id=self.policy_id)
            else:
                if (
                    db._sensitive_record_integrity_row_exists(conn, "permission_policies", self.policy_id)
                    or _permission_policy_presence_exists(conn, self.policy_id)
                    or (_sensitive_integrity_anchor_complete(conn) and self.policy_id == "default")
                ):
                    raise db.SensitiveRecordIntegrityError(
                        f"Sensitive permission policy record is missing for {self.policy_id}"
                    )
                policy = PermissionPolicy(id=self.policy_id)
            policy.id = self.policy_id
            policy.rules = [existing for existing in policy.rules if existing.id != model.id]
            policy.rules.append(model)
            policy.updated_at = now_iso()
            serialized = policy.model_dump_json()
            conn.execute(
                """
                INSERT INTO permission_policies (id, data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
                """,
                (self.policy_id, serialized, policy.updated_at),
            )
            db.store_sensitive_record_integrity("permission_policies", self.policy_id, serialized, conn=conn)
        return policy

    def upsert_rule(self, rule: PermissionRule | dict[str, Any]) -> PermissionPolicy:
        return self.add_rule(rule)

    def delete_rule(self, rule_id: str) -> tuple[PermissionPolicy, bool]:
        policy = self.get_policy()
        before = len(policy.rules)
        policy.rules = [rule for rule in policy.rules if rule.id != rule_id]
        saved = self.save_policy(policy)
        return saved, len(saved.rules) != before

    def evaluate(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        context: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> PermissionDecision:
        return evaluate_permission_policy(
            self.get_policy(),
            tool_name=tool_name,
            args=args,
            context=context,
            now=now,
        )

    def _ensure_schema(self) -> None:
        db.init_db()
        with db.connect() as conn:
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'permission_policies'"
            ).fetchone()
            proof_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (db.SENSITIVE_RECORD_INTEGRITY_TABLE,),
            ).fetchone()
            presence_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sensitive_record_presence'"
            ).fetchone()
            if table is None or proof_table is None or presence_table is None:
                raise db.SensitiveRecordIntegrityError("Sensitive permission policy schema is unavailable")


def _permission_policy_presence_exists(conn: Any, policy_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sensitive_record_presence WHERE table_name = 'permission_policies' AND record_id = ?",
        (policy_id,),
    ).fetchone()
    return row is not None


def _sensitive_integrity_anchor_complete(conn: Any) -> bool:
    row = conn.execute("SELECT state FROM sensitive_integrity_bootstrap_anchor WHERE id = 1").fetchone()
    return row is not None and str(row["state"] or "") == "complete"


def evaluate_permission_policy(
    policy: PermissionPolicy,
    *,
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> PermissionDecision:
    assert_capability_allowed(
        "permission_policy",
        policy.id,
        payload=permission_policy_capability_payload(policy),
    )
    context = context or {}
    current_time = now or _context_datetime(context)
    raw_paths = list(_candidate_paths(args))
    _, invalid_paths = _canonical_candidate_paths(raw_paths)
    if invalid_paths:
        return PermissionDecision(
            allowed=False,
            matched=True,
            effect="deny",
            rule_id=BUILTIN_AMBIGUOUS_PATH_RULE_ID,
            rule_name=BUILTIN_AMBIGUOUS_PATH_RULE_NAME,
            reason="Ambiguous or unsafe path argument rejected before permission matching.",
        )
    matching: list[PermissionRule] = []
    for rule in policy.rules:
        if not rule.enabled:
            continue
        if not _tool_matches(rule, tool_name):
            continue
        if not _path_matches(rule, args):
            continue
        if not _time_matches(rule, current_time):
            continue
        matching.append(rule)

    deny = next((rule for rule in matching if rule.effect == "deny"), None)
    if deny:
        return _decision(False, deny)
    allow = next((rule for rule in matching if rule.effect == "allow"), None)
    if allow:
        return _decision(True, allow)
    builtin_deny = _builtin_baseline_deny_decision(tool_name)
    if builtin_deny:
        return builtin_deny
    has_allow_rules = any(rule.enabled and rule.effect == "allow" for rule in policy.rules)
    if has_allow_rules:
        return PermissionDecision(
            allowed=False,
            matched=False,
            reason="No permission rule matched; allow-list policy default deny.",
        )
    return PermissionDecision(
        allowed=True,
        matched=False,
        reason="No deny rule matched; default allow.",
    )


def _parse_stored_permission_policy(data: str, *, policy_id: str) -> PermissionPolicy:
    try:
        policy = PermissionPolicy.model_validate(json.loads(data))
    except (json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise db.SensitiveRecordIntegrityError(
            f"Sensitive permission policy payload is invalid for {policy_id}"
        ) from exc
    return policy


def _builtin_baseline_deny_decision(tool_name: str) -> PermissionDecision | None:
    normalized = str(tool_name or "").casefold()
    if not normalized:
        return None
    if not any(fnmatch.fnmatchcase(normalized, pattern.casefold()) for pattern in _BUILTIN_BASELINE_DENY_PATTERNS):
        return None
    return PermissionDecision(
        allowed=False,
        matched=True,
        effect="deny",
        rule_id=BUILTIN_BASELINE_DENY_RULE_ID,
        rule_name=BUILTIN_BASELINE_DENY_RULE_NAME,
        reason=("Built-in high-risk baseline denied this tool. Add an explicit allow permission rule to enable it."),
    )


def evaluate_user_permission_for_tool(
    *,
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
    policy_engine: Any | None = None,
    permission_store: PermissionStore | None = None,
    now: datetime | None = None,
) -> PermissionDecision:
    """Single evaluation path for user permission rules (PolicyEngine + ToolRuntime)."""
    if policy_engine is None:
        store = permission_store or PermissionStore()
        return store.evaluate(tool_name=tool_name, args=args, context=context, now=now)
    policy_override = getattr(policy_engine, "permission_policy", None)
    store = getattr(policy_engine, "permission_store", None) or permission_store or PermissionStore()
    effective_now = now
    now_provider = getattr(policy_engine, "now_provider", None)
    if effective_now is None and callable(now_provider):
        effective_now = now_provider()
    if policy_override is not None:
        decision = evaluate_permission_policy(
            policy_override,
            tool_name=tool_name,
            args=args,
            context=context,
            now=effective_now,
        )
    else:
        decision = store.evaluate(tool_name=tool_name, args=args, context=context, now=effective_now)
    if is_builtin_baseline_deny(decision):
        return PermissionDecision(
            allowed=True,
            matched=True,
            rule_id=decision.rule_id,
            rule_name=decision.rule_name,
            reason="Built-in high-risk baseline deferred to the engine approval chain.",
        )
    return decision


def weekend_delete_rule() -> PermissionRule:
    return PermissionRule(
        name="Weekend delete block",
        effect="deny",
        tool="file.trash",
        path_pattern="*",
        time_window=PermissionTimeWindow(start="00:00", end="23:59", days=["weekend"]),
        reason="Deleting files is disabled on weekends.",
    )


def _decision(allowed: bool, rule: PermissionRule) -> PermissionDecision:
    return PermissionDecision(
        allowed=allowed,
        matched=True,
        effect=rule.effect,
        rule_id=rule.id,
        rule_name=rule.name,
        reason=rule.reason or f"Permission rule '{rule.name or rule.id}' matched.",
    )


def _tool_matches(rule: PermissionRule, tool_name: str) -> bool:
    if not rule.tools:
        return True
    normalized = tool_name.casefold()
    return any(fnmatch.fnmatchcase(normalized, pattern.casefold()) for pattern in rule.tools)


def _path_matches(rule: PermissionRule, args: dict[str, Any]) -> bool:
    if not rule.path_patterns:
        return True
    paths, invalid_paths = _canonical_candidate_paths(_candidate_paths(args))
    if invalid_paths:
        return False
    if not paths:
        return any(canonicalize_path(pattern, allow_glob=True) in {"*", "**"} for pattern in rule.path_patterns)
    normalized_patterns = [
        normalized
        for pattern in rule.path_patterns
        if (normalized := canonicalize_path(pattern, allow_glob=True)) is not None
    ]
    if not normalized_patterns:
        return False
    matches = [any(fnmatch.fnmatchcase(path, pattern) for pattern in normalized_patterns) for path in paths]
    # A deny rule is triggered by any covered resource.  An allow rule grants
    # the operation only when every resource argument is covered; otherwise a
    # second source/destination can smuggle an out-of-scope path through an
    # "any match" check.
    return all(matches) if rule.effect == "allow" else any(matches)


def _time_matches(rule: PermissionRule, now: datetime) -> bool:
    if not rule.time_windows:
        return True
    return any(_window_matches(window, now) for window in rule.time_windows)


def _window_matches(window: PermissionTimeWindow, now: datetime) -> bool:
    current_dt = _window_datetime(window, now)
    if window.days and not _day_matches(window.days, current_dt.weekday()):
        return False
    start = _minutes(window.start)
    end = _minutes(window.end)
    current = current_dt.hour * 60 + current_dt.minute
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _window_datetime(window: PermissionTimeWindow, now: datetime) -> datetime:
    if not window.timezone:
        return now
    try:
        zone = ZoneInfo(window.timezone)
    except ZoneInfoNotFoundError:
        return now
    return now.astimezone(zone) if now.tzinfo else now.replace(tzinfo=zone)


def _minutes(value: str) -> int:
    hour, minute = _parse_clock(value)
    return hour * 60 + minute


def _parse_clock(value: str) -> tuple[int, int]:
    raw = str(value or "00:00").strip()
    parts = raw.split(":", 1)
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        hour, minute = 0, 0
    return max(0, min(hour, 23)), max(0, min(minute, 59))


def _normalize_day(value: int | str) -> int | str | None:
    if isinstance(value, int):
        return value if 0 <= value <= 6 else None
    text = str(value).strip().lower()
    if not text:
        return None
    aliases = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    if text in {"weekend", "weekday"}:
        return text
    if text in aliases:
        return aliases[text]
    try:
        day = int(text)
    except ValueError:
        return None
    return day if 0 <= day <= 6 else None


def _day_matches(days: list[int | str], weekday: int) -> bool:
    for day in days:
        if day == "weekend" and weekday in {5, 6}:
            return True
        if day == "weekday" and weekday in {0, 1, 2, 3, 4}:
            return True
        if isinstance(day, int) and day == weekday:
            return True
    return False


def _candidate_paths(value: Any) -> list[str]:
    return candidate_paths(value)


def _canonical_candidate_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    canonical: list[str] = []
    invalid: list[str] = []
    for path in paths:
        normalized = canonicalize_path(path)
        if normalized is None:
            invalid.append(path)
        else:
            canonical.append(normalized)
    return canonical, invalid


def _context_datetime(context: dict[str, Any]) -> datetime:
    raw = context.get("now") or context.get("current_time") or context.get("timestamp")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now().astimezone()
    return datetime.now().astimezone()
