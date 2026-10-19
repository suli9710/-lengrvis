"""Shared Butler serialization, wait snapshots and time comparisons."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.core.butler_models import (
    AutonomyScope,
    ButlerMatter,
    MatterStatus,
    WaitCondition,
)
from app.llm.registry import get_effective_settings
from app.services.butler_scopes import matter_scopes


def _credential_answers(value) -> bool:
    if isinstance(value, dict):
        return any(
            (
                bool(item)
                and re.search(r"password|passwd|api[_-]?key|secret|token|cookie|验证码|密码|密钥|口令", str(key), re.I)
            )
            or _credential_answers(item)
            for key, item in value.items()
        )
    return isinstance(value, list) and any(_credential_answers(item) for item in value)


def public_matter(matter: ButlerMatter) -> dict:
    payload = matter.model_dump(
        mode="json",
        exclude={"execution_ticket", "pending_notifications", "memory_inputs", "memory_observation_keys", "artifacts"},
    )
    from app.policy.redaction import redact_value

    sanitized = redact_value(payload)
    from app.services.office_artifacts import public_artifacts

    sanitized["artifacts"] = public_artifacts(matter)
    # Typed opaque control identifiers are not user-authored token strings.
    for key in (
        "id",
        "active_task_id",
        "active_run_id",
        "task_ids",
        "run_ids",
        "scope_versions",
        "depends_on",
        "dependency_revisions",
        "memory_ids",
    ):
        sanitized[key] = payload[key]
    if matter.interaction:
        for key in ("id", "candidates", "approval_id", "fields"):
            sanitized["interaction"][key] = payload["interaction"][key]
    coordination = payload.get("constraints", {}).get("coordination_request")
    if isinstance(coordination, dict):
        for key in ("parents", "children", "versions"):
            if key in coordination:
                sanitized["constraints"]["coordination_request"][key] = coordination[key]
    for original, visible in zip(payload["completion_evidence"], sanitized["completion_evidence"], strict=True):
        for key in ("memory_id", "tool_call_id"):
            if key in original:
                visible[key] = original[key]
    return sanitized


def _file_wait_state(matter: ButlerMatter, wait: WaitCondition) -> str | None:
    from pathlib import Path

    from app.services.butler_scopes import path_in_scope

    scopes = matter_scopes(matter)
    if matter.source == "user":
        scopes = [
            *scopes,
            AutonomyScope(label="用户任务目录", directories=get_effective_settings().allowed_directories),
        ]
    if not any("observe" in scope.allowed_actions and path_in_scope(wait.subject, scope) for scope in scopes):
        return None
    path = Path(wait.subject)
    if not path.is_file():
        return "absent"
    stat = path.stat()
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _set_status(matter: ButlerMatter, status: MatterStatus, progress: str) -> None:
    matter.status = status
    matter.progress = progress


def _after_seconds(seconds: int) -> str:
    from datetime import timedelta

    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


def _due(value: str) -> bool:
    instant = _parse_time(value)
    return instant is not None and instant <= datetime.now(UTC)


def _after(value: str, baseline: str) -> bool:
    first, second = _parse_time(value), _parse_time(baseline)
    return bool(first is not None and second is not None and first > second)


def _priority(matter: ButlerMatter):
    deadline = _parse_time(matter.due_at)
    urgent = bool(deadline and deadline <= _parse_time(_after_seconds(3600)))
    return (
        0 if matter.source == "user" else 1 if urgent else 2 if matter.active_run_id else 3,
        -matter.priority,
        matter.created_at,
    )
