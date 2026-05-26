from __future__ import annotations

import fnmatch
import json
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from app.core import db
from app.core.schemas import now_iso


PermissionEffect = Literal["allow", "deny"]


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
    ) -> "PermissionDecision":
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


class PermissionStore:
    def __init__(self, policy_id: str = "default") -> None:
        self.policy_id = policy_id

    def get_policy(self) -> PermissionPolicy:
        self._ensure_schema()
        with db.connect() as conn:
            row = conn.execute("SELECT data FROM permission_policies WHERE id = ?", (self.policy_id,)).fetchone()
        if not row:
            return PermissionPolicy(id=self.policy_id)
        try:
            return PermissionPolicy.model_validate(json.loads(row["data"]))
        except Exception:
            return PermissionPolicy()

    def updated_at(self) -> str:
        self._ensure_schema()
        with db.connect() as conn:
            row = conn.execute("SELECT updated_at FROM permission_policies WHERE id = ?", (self.policy_id,)).fetchone()
        return str(row["updated_at"]) if row else ""

    def save_policy(self, policy: PermissionPolicy | dict[str, Any]) -> PermissionPolicy:
        self._ensure_schema()
        model = PermissionPolicy.model_validate(policy)
        model.id = self.policy_id
        model.updated_at = now_iso()
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO permission_policies (id, data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
                """,
                (self.policy_id, model.model_dump_json(), model.updated_at),
            )
        return model

    def add_rule(self, rule: PermissionRule | dict[str, Any]) -> PermissionPolicy:
        model = PermissionRule.model_validate(rule)
        policy = self.get_policy()
        policy.rules = [existing for existing in policy.rules if existing.id != model.id]
        policy.rules.append(model)
        return self.save_policy(policy)

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS permission_policies (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )


def evaluate_permission_policy(
    policy: PermissionPolicy,
    *,
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> PermissionDecision:
    context = context or {}
    current_time = now or _context_datetime(context)
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
    return PermissionDecision(allowed=True)


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
    paths = list(_candidate_paths(args))
    if not paths:
        return any(pattern in {"*", "**"} for pattern in rule.path_patterns)
    normalized_patterns = [pattern.replace("\\", "/").casefold() for pattern in rule.path_patterns]
    for path in paths:
        normalized = path.replace("\\", "/").casefold()
        if any(fnmatch.fnmatchcase(normalized, pattern) for pattern in normalized_patterns):
            return True
    return False


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
    except Exception:
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
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if "path" in str(key).casefold() or str(key) in {"source", "destination", "target", "folder", "directory"}:
                result.extend(_candidate_paths(item))
            elif isinstance(item, (dict, list, tuple)):
                result.extend(_candidate_paths(item))
        return result
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(_candidate_paths(item))
        return result
    if isinstance(value, str) and value.strip():
        result.append(value.strip())
    return result


def _context_datetime(context: dict[str, Any]) -> datetime:
    raw = context.get("now") or context.get("current_time") or context.get("timestamp")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now().astimezone()
