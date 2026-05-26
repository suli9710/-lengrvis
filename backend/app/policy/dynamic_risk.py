from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Any

from app.policy.risk import RISK_ORDER, RiskLevel


RISK_BY_SCORE = {score: level for level, score in RISK_ORDER.items()}
PATH_ARG_KEYS = {
    "path",
    "paths",
    "source",
    "sources",
    "destination",
    "destinations",
    "target",
    "target_path",
    "target_folder",
    "folder",
    "directory",
    "output_path",
    "file",
    "files",
}


@dataclass(slots=True)
class DynamicRiskAssessment:
    base_risk: RiskLevel
    adjusted_risk: RiskLevel
    reasons: list[str] = field(default_factory=list)
    factors: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.adjusted_risk != self.base_risk

    @property
    def risk_level(self) -> RiskLevel:
        return self.adjusted_risk

    @property
    def adjustments(self) -> list[str]:
        return [reason for reason in self.reasons if reason and not reason.startswith("Static risk classified")]


class DynamicRiskAssessor:
    """Adjusts static tool risk using runtime context.

    The assessor is intentionally deterministic and conservative. It may raise
    risk above the static level when context is concerning, but it never turns
    an R4 forbidden/handoff operation into a lower tier.
    """

    late_night_start_hour = 22
    late_night_end_hour = 5

    def assess(
        self,
        tool_name: str,
        args: dict[str, Any],
        base_risk: RiskLevel,
        context: dict[str, Any] | None = None,
        task_id: str = "",
    ) -> DynamicRiskAssessment:
        context = context or {}
        if base_risk == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
            return DynamicRiskAssessment(
                base_risk=base_risk,
                adjusted_risk=base_risk,
                reasons=["Static risk is forbidden/handoff-only; dynamic risk cannot lower it."],
                factors={"task_id": task_id, "tool_name": tool_name},
            )

        score = RISK_ORDER[base_risk]
        reasons: list[str] = [f"Static risk classified {tool_name} as {base_risk.value}."]
        factors: dict[str, Any] = {"task_id": task_id, "tool_name": tool_name}

        paths = list(_candidate_paths(args))
        path_factor = self._path_factor(paths)
        factors["paths"] = paths
        factors["path_category"] = path_factor
        if path_factor == "system":
            score += 2 if RISK_ORDER[base_risk] <= RISK_ORDER[RiskLevel.R1_OPEN_ONLY] else 1
            reasons.append("Target path is in a system or application directory.")
        elif path_factor == "user_documents":
            reasons.append("Target path appears to be in user-owned documents or desktop storage.")

        now = _context_datetime(context)
        factors["hour"] = now.hour if now else None
        if now and self._is_late_night(now):
            score += 1
            reasons.append("Deep-night operation increases review risk.")

        failures = _recent_failure_count(context)
        factors["recent_failures"] = failures
        if failures >= 3:
            score += 2
            reasons.append("Multiple recent failures increase execution risk.")
        elif failures > 0:
            score += 1
            reasons.append("Recent failure history increases execution risk.")

        trust = _trust_level(context)
        factors["user_trust_level"] = trust
        if trust in {"low", "unknown"}:
            score += 1
            reasons.append("Low or unknown user trust level increases risk.")
        elif trust == "high" and score > RISK_ORDER[base_risk] and path_factor != "system":
            score -= 1
            reasons.append("High user trust level softens one non-system contextual risk uplift.")

        adjusted = _risk_from_score(score)
        if adjusted != base_risk:
            reasons.append(f"Dynamic risk adjusted {base_risk.value} to {adjusted.value}.")
        else:
            reasons.append("Dynamic context did not change the static risk tier.")

        return DynamicRiskAssessment(
            base_risk=base_risk,
            adjusted_risk=adjusted,
            reasons=reasons,
            factors=factors,
        )

    def _is_late_night(self, value: datetime) -> bool:
        hour = value.hour
        return hour >= self.late_night_start_hour or hour <= self.late_night_end_hour

    def _is_write_like(self, tool_name: str, args: dict[str, Any], base_risk: RiskLevel) -> bool:
        if RISK_ORDER[base_risk] >= RISK_ORDER[RiskLevel.R2_REVERSIBLE_MODIFY]:
            return True
        if args.get("dry_run") is False:
            return True
        lowered = tool_name.lower()
        return any(
            token in lowered
            for token in (
                ".write",
                ".move",
                ".rename",
                ".trash",
                ".delete",
                ".click",
                ".type",
                ".submit",
                "uninstall",
            )
        )

    def _path_factor(self, paths: list[str]) -> str:
        for path in paths:
            if _is_system_path(path):
                return "system"
        for path in paths:
            if _is_user_document_path(path):
                return "user_documents"
        return "unspecified" if not paths else "other"


def _risk_from_score(score: int) -> RiskLevel:
    clamped = max(0, min(score, RISK_ORDER[RiskLevel.R4_FORBIDDEN_OR_HANDOFF]))
    return RISK_BY_SCORE[clamped]


def _candidate_paths(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).casefold()
            if normalized_key in PATH_ARG_KEYS or "path" in normalized_key:
                result.extend(_candidate_paths(item))
            elif isinstance(item, (dict, list, tuple)):
                result.extend(_candidate_paths(item))
        return result
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(_candidate_paths(item))
        return result
    if isinstance(value, str):
        text = value.strip()
        if text:
            result.append(text)
    return result


def _context_datetime(context: dict[str, Any]) -> datetime | None:
    raw = context.get("now") or context.get("current_time") or context.get("timestamp")
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def _recent_failure_count(context: dict[str, Any]) -> int:
    raw = (
        context.get("recent_failures")
        or context.get("recent_failure_count")
        or context.get("failure_count")
        or context.get("failed_attempts")
        or 0
    )
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, (list, tuple, set)):
        return len(raw)
    try:
        return max(0, int(str(raw)))
    except (TypeError, ValueError):
        return 0


def _trust_level(context: dict[str, Any]) -> str:
    raw = context.get("user_trust_level", context.get("trust_level", context.get("user_trust", "medium")))
    if isinstance(raw, (int, float)):
        if raw >= 0.75:
            return "high"
        if raw <= 0.35:
            return "low"
        return "medium"
    value = str(raw or "medium").strip().lower()
    if value in {"trusted", "high", "admin"}:
        return "high"
    if value in {"low", "unknown", "guest", "untrusted"}:
        return "low"
    return "medium"


def _is_system_path(path: str) -> bool:
    normalized = _normalized_path(path)
    system_prefixes = (
        "c:/windows",
        "c:/program files",
        "c:/program files (x86)",
        "c:/programdata",
        "/windows",
        "/program files",
        "/programdata",
        "/etc",
        "/bin",
        "/sbin",
        "/usr",
        "/var",
        "/system",
        "/library",
    )
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in system_prefixes)


def _is_user_document_path(path: str) -> bool:
    normalized = _normalized_path(path)
    markers = (
        "/documents/",
        "/desktop/",
        "/downloads/",
        "/pictures/",
        "/videos/",
        "/music/",
    )
    return any(marker in f"{normalized}/" for marker in markers)


def _normalized_path(path: str) -> str:
    text = path.strip().replace("\\", "/")
    if not text:
        return ""
    try:
        pure = PureWindowsPath(text)
        if pure.drive:
            text = pure.as_posix()
    except (TypeError, ValueError):
        pass
    return text.rstrip("/").casefold()
