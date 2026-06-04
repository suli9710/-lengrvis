from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from app.core import db
from app.core.schemas import Wakeup, WakeupStatus, now_iso
from app.policy.redaction import redact_value
from app.services import notification_service


def list_pending_wakeups() -> list[dict[str, Any]]:
    return db.fetch_many("wakeups", "status = ?", (WakeupStatus.PENDING.value,), limit=200)


def list_pending_mobile_wakeups() -> list[dict[str, Any]]:
    return [safe_wakeup_payload(item) for item in list_pending_wakeups()]


def get_wakeup(wakeup_id: str) -> Wakeup:
    data = db.fetch_one("wakeups", wakeup_id)
    if not data:
        raise HTTPException(status_code=404, detail="Wakeup not found")
    return Wakeup.model_validate(data)


def create_schedule_wakeup(schedule: Any, *, due_at: str) -> Wakeup:
    wakeup, created = _create_schedule_wakeup_atomically(schedule, due_at=due_at)
    if not created:
        return wakeup
    notification_service.notify(
        "Scheduled task ready",
        schedule.goal,
        task_id=wakeup.id,
        severity="info",
    )
    return wakeup


def _create_schedule_wakeup_atomically(schedule: Any, *, due_at: str) -> tuple[Wakeup, bool]:
    wakeup = Wakeup(
        source="schedule",
        source_id=schedule.id,
        title="Scheduled task ready",
        body=schedule.goal,
        goal=schedule.goal,
        mode=schedule.mode,
        due_at=due_at,
    )
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT data FROM wakeups
            WHERE source = ? AND source_id = ? AND status = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            ("schedule", schedule.id, WakeupStatus.PENDING.value),
        ).fetchone()
        if existing is not None:
            return Wakeup.model_validate(json.loads(existing["data"])), False

        data = wakeup.model_dump(mode="json")
        conn.execute(
            """
            INSERT INTO wakeups (id, source, source_id, status, due_at, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["id"],
                data["source"],
                data["source_id"],
                data["status"],
                data["due_at"],
                json.dumps(data, ensure_ascii=False),
                data["created_at"],
                data["updated_at"],
            ),
        )
    return wakeup, True


def approve_wakeup(wakeup_id: str) -> Wakeup:
    return _decide_wakeup_atomically(wakeup_id, WakeupStatus.APPROVED)


def reject_wakeup(wakeup_id: str) -> Wakeup:
    return _decide_wakeup_atomically(wakeup_id, WakeupStatus.REJECTED)


def _decide_wakeup_atomically(wakeup_id: str, status: WakeupStatus) -> Wakeup:
    timestamp = now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT data FROM wakeups WHERE id = ?", (wakeup_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Wakeup not found")

        wakeup = Wakeup.model_validate(json.loads(row["data"]))
        if wakeup.status != WakeupStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"Wakeup is already {wakeup.status}.")
        if status == WakeupStatus.APPROVED:
            _raise_if_wakeup_source_inactive(conn, wakeup)

        wakeup.status = status
        wakeup.decided_at = timestamp
        wakeup.updated_at = timestamp
        data = wakeup.model_dump(mode="json")
        cursor = conn.execute(
            """
            UPDATE wakeups
            SET status = ?,
                data = ?,
                updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (status.value, json.dumps(data, ensure_ascii=False), timestamp, wakeup_id, WakeupStatus.PENDING.value),
        )
        if cursor.rowcount != 1:
            current_row = conn.execute("SELECT data FROM wakeups WHERE id = ?", (wakeup_id,)).fetchone()
            current = Wakeup.model_validate(json.loads(current_row["data"])) if current_row else wakeup
            raise HTTPException(status_code=409, detail=f"Wakeup is already {current.status}.")
    return wakeup


def _raise_if_wakeup_source_inactive(conn: Any, wakeup: Wakeup) -> None:
    if wakeup.source != "schedule" or not wakeup.source_id:
        return
    schedule = conn.execute("SELECT enabled FROM scheduled_tasks WHERE id = ?", (wakeup.source_id,)).fetchone()
    if not schedule:
        raise HTTPException(status_code=409, detail="Wakeup source schedule is no longer available.")
    if not bool(schedule["enabled"]):
        raise HTTPException(status_code=409, detail="Wakeup source schedule is disabled.")


def complete_wakeup(wakeup: Wakeup, *, run_id: str = "", error: str = "") -> Wakeup:
    wakeup.status = WakeupStatus.FAILED if error else WakeupStatus.COMPLETED
    wakeup.run_id = run_id
    wakeup.error = str(redact_value(error)) if error else ""
    wakeup.updated_at = now_iso()
    db.upsert_model("wakeups", wakeup)
    return wakeup


def safe_wakeup_payload(wakeup: Wakeup | dict[str, Any]) -> dict[str, Any]:
    payload = wakeup.model_dump(mode="json") if isinstance(wakeup, Wakeup) else dict(wakeup)
    for key in ("title", "body", "goal", "error"):
        payload[key] = redact_value(payload.get(key) or "")
    return payload
