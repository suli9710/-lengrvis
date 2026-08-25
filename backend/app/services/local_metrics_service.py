"""Opt-in, local-only execution quality metrics.

Aggregates raw data that the product already records (tasks, runs, run_events,
llm_usage_events) into the four headline rates the desktop metrics panel shows:

- task success rate (completed / terminal tasks)
- recovery trigger rate (runs that needed at least one OS reflection)
- ask_user reflection share (reflections that escalated to the user)
- LLM anomaly rate (recorded responses with a non-success finish reason)

Privacy posture: this module only counts; it never returns goals, prompts,
file paths, or any free-text payloads, and the API endpoint is gated behind the
``local_metrics_enabled`` opt-in setting.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core import db

TERMINAL_TASK_STATUSES = {"completed", "failed", "denied", "cancelled", "rolled_back", "repair_required"}
SUCCESS_TASK_STATUSES = {"completed", "rolled_back"}

# finish_reason values that indicate a normal completion across providers.
OK_FINISH_REASONS = {"", "stop", "end_turn", "completed", "tool_calls", "tool_use", "length", "max_tokens"}

REFLECTION_STARTED_EVENT = "os.reflection.started"
REFLECTION_DECIDED_EVENT = "os.reflection.decided"


def collect_local_metrics(*, days: int = 7) -> dict[str, Any]:
    window_days = max(1, min(90, int(days)))
    since = (datetime.now(UTC) - timedelta(days=window_days)).isoformat()
    db.init_db()
    with db.connect() as conn:
        task_rows = conn.execute(
            "SELECT data FROM tasks WHERE created_at >= ?",
            (since,),
        ).fetchall()
        run_rows = conn.execute(
            "SELECT id, phase FROM runs WHERE created_at >= ?",
            (since,),
        ).fetchall()
        reflection_rows = conn.execute(
            "SELECT run_id, name, data FROM run_events WHERE created_at >= ? AND name IN (?, ?)",
            (since, REFLECTION_STARTED_EVENT, REFLECTION_DECIDED_EVENT),
        ).fetchall()
        llm_rows = conn.execute(
            "SELECT data, estimated FROM llm_usage_events WHERE created_at >= ?",
            (since,),
        ).fetchall()

    tasks = _task_metrics(task_rows)
    runs = _run_metrics(run_rows)
    reflections = _reflection_metrics(reflection_rows, total_runs=runs["total"])
    llm = _llm_metrics(llm_rows)

    return {
        "window_days": window_days,
        "since": since,
        "generated_at": datetime.now(UTC).isoformat(),
        "tasks": tasks,
        "runs": runs,
        "recovery": reflections,
        "llm": llm,
    }


def _task_metrics(rows: list[Any]) -> dict[str, Any]:
    status_counts: Counter[str] = Counter()
    for row in rows:
        data = _json_dict(row["data"])
        status = _effective_task_status(data)
        if status:
            status_counts[status] += 1
    terminal = sum(count for status, count in status_counts.items() if status in TERMINAL_TASK_STATUSES)
    succeeded = sum(count for status, count in status_counts.items() if status in SUCCESS_TASK_STATUSES)
    return {
        "total": len(rows),
        "terminal": terminal,
        "succeeded": succeeded,
        "success_rate": _rate(succeeded, terminal),
        "by_status": dict(sorted(status_counts.items())),
    }


def _effective_task_status(data: dict[str, Any]) -> str:
    status = str(data.get("status") or "").strip().lower()
    rollback = (data.get("metadata") or {}).get("rollback")
    if status == "failed" and isinstance(rollback, dict) and rollback:
        return "rolled_back" if str(rollback.get("state") or "").strip().lower() == "succeeded" else "repair_required"
    return status


def _run_metrics(rows: list[Any]) -> dict[str, Any]:
    phase_counts: Counter[str] = Counter()
    for row in rows:
        phase_counts[str(row["phase"] or "").strip().lower() or "unknown"] += 1
    return {
        "total": len(rows),
        "by_phase": dict(sorted(phase_counts.items())),
    }


def _reflection_metrics(rows: list[Any], *, total_runs: int) -> dict[str, Any]:
    started = 0
    decided_actions: Counter[str] = Counter()
    runs_with_reflection: set[str] = set()
    for row in rows:
        name = str(row["name"] or "")
        run_id = str(row["run_id"] or "")
        if name == REFLECTION_STARTED_EVENT:
            started += 1
            if run_id:
                runs_with_reflection.add(run_id)
        elif name == REFLECTION_DECIDED_EVENT:
            data = _json_dict(row["data"])
            # run_events rows persist the whole event dict; the emitted fields
            # live under "payload" (older rows may carry them at the top level).
            payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
            action = str(payload.get("action") or "unknown").strip().lower() or "unknown"
            decided_actions[action] += 1
    decided_total = sum(decided_actions.values())
    ask_user = decided_actions.get("ask_user", 0)
    return {
        "reflections_started": started,
        "runs_with_reflection": len(runs_with_reflection),
        "recovery_trigger_rate": _rate(len(runs_with_reflection), total_runs),
        "decided_actions": dict(sorted(decided_actions.items())),
        "ask_user_share": _rate(ask_user, decided_total),
    }


def _llm_metrics(rows: list[Any]) -> dict[str, Any]:
    total = len(rows)
    anomalies = 0
    estimated = 0
    finish_reasons: Counter[str] = Counter()
    for row in rows:
        if _safe_int(row["estimated"]):
            estimated += 1
        data = _json_dict(row["data"])
        finish_reason = str(data.get("finish_reason") or "").strip().lower()
        finish_reasons[finish_reason or "(none)"] += 1
        if finish_reason not in OK_FINISH_REASONS:
            anomalies += 1
    return {
        "calls": total,
        "anomalies": anomalies,
        "anomaly_rate": _rate(anomalies, total),
        "estimated_calls": estimated,
        "by_finish_reason": dict(sorted(finish_reasons.items())),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
