from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

_LEGACY_DENIED_TASK_SUMMARY_PREFIXES = (
    "automated execution was denied:",
    "denied:",
    "deterministic plan integrity verification failed.",
    "forbidden intent detected.",
    "safetyreviewagent stopped the task",
    "task denied by safety review",
    "tool dry-run preview did not satisfy the approval safety contract.",
    "tool execution was denied:",
    "tool requires approval but does not support a safe dry-run preview.",
)
_LEGACY_EXPLICIT_CANCELLATION_MARKERS = (
    "approval was rejected by the user",
    "approval rejected by the user",
    "cancelled by the user",
    "cancelled by user",
    "canceled by the user",
    "canceled by user",
    "run cancelled",
    "run canceled",
)
_TERMINAL_RUN_EVENT_NAMES = frozenset({"run.completed", "run.failed", "run.denied", "run.cancelled"})


@dataclass(frozen=True)
class _LatestRun:
    id: str
    task_id: str
    phase: str
    data: Any
    updated_at: str
    terminal_event: str = ""


def task_denied_phase_backfill(conn: sqlite3.Connection) -> None:
    """Reconcile legacy denial aliases using only the latest task/run evidence."""

    if not _table_exists(conn, "tasks"):
        return
    latest_runs = _latest_runs_by_task(conn)
    rows = conn.execute("SELECT id, data FROM tasks ORDER BY id").fetchall()
    for row in rows:
        task_id = str(row[0])
        payload = _safe_json_payload(row[1])
        if not payload:
            continue
        status = _text(payload.get("status")).casefold()
        phase = _text(payload.get("phase")).casefold()
        if status not in {"cancelled", "denied"} and phase not in {
            "cancelled",
            "denied",
        }:
            continue

        summary = _text(payload.get("final_summary")).casefold()
        latest_run = latest_runs.get(task_id)
        target = _reconciled_phase(
            status=status,
            phase=phase,
            summary=summary,
            latest_run=latest_run,
        )
        if target is None:
            continue

        payload.update(status=target, phase=target, execution_stage="idle")
        conn.execute(
            "UPDATE tasks SET data = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), task_id),
        )
        if latest_run is not None:
            _align_run_phase(conn, latest_run, target)


def _reconciled_phase(
    *,
    status: str,
    phase: str,
    summary: str,
    latest_run: _LatestRun | None,
) -> str | None:
    # The persisted phase is the current run state.  Never let an older event
    # terminate a run that was subsequently completed, failed, or resumed.
    if latest_run is not None:
        if latest_run.phase not in {"cancelled", "denied"}:
            return None

        # Compare every terminal outcome.  Filtering this history to only
        # denied/cancelled can hide a later completion or failure and corrupt
        # the run back to an older phase.
        if latest_run.terminal_event in {"run.completed", "run.failed"}:
            return None
        if latest_run.terminal_event == "run.cancelled":
            return "cancelled"
        if latest_run.terminal_event == "run.denied":
            return "denied"

        if latest_run.phase == "denied":
            # Old v9 could rewrite a cancelled run to denied without writing a
            # run event.  An explicit user-cancellation summary is the one
            # narrow repair signal we retain for that already-corrupted shape.
            if _is_explicit_cancellation_summary(summary):
                return "cancelled"
            return "denied"

        # A legacy cancelled run is denied only when the task itself carries
        # unambiguous denial evidence; otherwise preserve the cancellation.
        if status == "denied" or phase == "denied" or summary.startswith(_LEGACY_DENIED_TASK_SUMMARY_PREFIXES):
            return "denied"
        return "cancelled"

    # With no run evidence, retain the conservative legacy backfill behavior.
    # Explicit cancellation markers are checked first to repair databases that
    # were already rewritten by v9 before this reconciliation migration ran.
    if _is_explicit_cancellation_summary(summary):
        return "cancelled"
    if status == "denied" or phase == "denied":
        return "denied"
    if status == "cancelled" and summary.startswith(_LEGACY_DENIED_TASK_SUMMARY_PREFIXES):
        return "denied"
    return None


def _latest_runs_by_task(conn: sqlite3.Connection) -> dict[str, _LatestRun]:
    if not _table_exists(conn, "runs"):
        return {}
    terminal_events = _latest_terminal_events_by_run(conn)
    latest: dict[str, _LatestRun] = {}
    rows = conn.execute(
        """
        SELECT id, task_id, phase, data, updated_at
        FROM runs
        WHERE task_id IS NOT NULL
        ORDER BY task_id, updated_at DESC, id DESC
        """
    ).fetchall()
    for row in rows:
        task_id = str(row[1] or "").strip()
        if not task_id or task_id in latest:
            continue
        run_id = str(row[0])
        latest[task_id] = _LatestRun(
            id=run_id,
            task_id=task_id,
            phase=_text(row[2]).casefold(),
            data=row[3],
            updated_at=_text(row[4]),
            terminal_event=terminal_events.get(run_id, ""),
        )
    return latest


def _latest_terminal_events_by_run(conn: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(conn, "run_events"):
        return {}
    latest: dict[str, str] = {}
    rows = conn.execute(
        """
        SELECT run_id, name
        FROM run_events
        WHERE name IN ('run.completed', 'run.failed', 'run.denied', 'run.cancelled')
        ORDER BY run_id, sequence DESC
        """
    ).fetchall()
    for row in rows:
        run_id = str(row[0])
        name = str(row[1])
        if run_id not in latest and name in _TERMINAL_RUN_EVENT_NAMES:
            latest[run_id] = name
    return latest


def _align_run_phase(
    conn: sqlite3.Connection,
    run: _LatestRun,
    target: str,
) -> None:
    payload = _safe_json_payload(run.data)
    serialized = run.data
    if payload:
        payload["phase"] = target
        state = payload.get("state")
        if isinstance(state, dict):
            state["phase"] = target
        serialized = json.dumps(payload, ensure_ascii=False)
    conn.execute(
        "UPDATE runs SET phase = ?, data = ? WHERE id = ?",
        (target, serialized, run.id),
    )
    _append_terminal_event_if_missing(conn, run, target)


def _append_terminal_event_if_missing(
    conn: sqlite3.Connection,
    run: _LatestRun,
    target: str,
) -> None:
    if not _table_exists(conn, "run_events"):
        return
    event_name = f"run.{target}"
    if run.terminal_event == event_name:
        return
    sequence = (
        int(
            conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM run_events WHERE run_id = ?",
                (run.id,),
            ).fetchone()[0]
            or 0
        )
        + 1
    )
    event = {
        "id": f"runevt_task_denied_v9_{run.id}_{target}_{sequence}",
        "run_id": run.id,
        "name": event_name,
        "sequence": sequence,
        "payload": {
            "phase": target,
            "reason": "task_denied_phase_reconcile",
            "task_id": run.task_id,
        },
        "created_at": run.updated_at or "1970-01-01T00:00:00+00:00",
    }
    conn.execute(
        """
        INSERT INTO run_events (id, run_id, name, sequence, data, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            event["id"],
            event["run_id"],
            event["name"],
            event["sequence"],
            json.dumps(event, ensure_ascii=False),
            event["created_at"],
        ),
    )


def _is_explicit_cancellation_summary(summary: str) -> bool:
    return any(marker in summary for marker in _LEGACY_EXPLICIT_CANCELLATION_MARKERS)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _safe_json_payload(value: Any) -> dict[str, Any]:
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if isinstance(value, str) else ""
