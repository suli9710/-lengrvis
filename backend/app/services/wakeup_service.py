from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core import db
from app.core.schemas import Wakeup, WakeupStatus, now_iso
from app.services import notification_service


def list_pending_wakeups() -> list[dict[str, Any]]:
    return db.fetch_many("wakeups", "status = ?", (WakeupStatus.PENDING.value,), limit=200)


def get_wakeup(wakeup_id: str) -> Wakeup:
    data = db.fetch_one("wakeups", wakeup_id)
    if not data:
        raise HTTPException(status_code=404, detail="Wakeup not found")
    return Wakeup.model_validate(data)


def create_schedule_wakeup(schedule: Any, *, due_at: str) -> Wakeup:
    existing = _pending_for_source("schedule", schedule.id)
    if existing is not None:
        return existing
    wakeup = Wakeup(
        source="schedule",
        source_id=schedule.id,
        title="Scheduled task ready",
        body=schedule.goal,
        goal=schedule.goal,
        mode=schedule.mode,
        due_at=due_at,
    )
    db.upsert_model("wakeups", wakeup)
    notification_service.notify(
        "Scheduled task ready",
        schedule.goal,
        task_id=wakeup.id,
        severity="info",
    )
    return wakeup


def approve_wakeup(wakeup_id: str) -> Wakeup:
    wakeup = get_wakeup(wakeup_id)
    if wakeup.status != WakeupStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Wakeup is already {wakeup.status}.")
    wakeup.status = WakeupStatus.APPROVED
    wakeup.decided_at = now_iso()
    wakeup.updated_at = now_iso()
    db.upsert_model("wakeups", wakeup)
    return wakeup


def reject_wakeup(wakeup_id: str) -> Wakeup:
    wakeup = get_wakeup(wakeup_id)
    if wakeup.status != WakeupStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Wakeup is already {wakeup.status}.")
    wakeup.status = WakeupStatus.REJECTED
    wakeup.decided_at = now_iso()
    wakeup.updated_at = now_iso()
    db.upsert_model("wakeups", wakeup)
    return wakeup


def complete_wakeup(wakeup: Wakeup, *, run_id: str = "", error: str = "") -> Wakeup:
    wakeup.status = WakeupStatus.FAILED if error else WakeupStatus.COMPLETED
    wakeup.run_id = run_id
    wakeup.error = error
    wakeup.updated_at = now_iso()
    db.upsert_model("wakeups", wakeup)
    return wakeup


def _pending_for_source(source: str, source_id: str) -> Wakeup | None:
    for item in db.fetch_many(
        "wakeups",
        "source = ? AND source_id = ? AND status = ?",
        (source, source_id, WakeupStatus.PENDING.value),
        limit=1,
    ):
        return Wakeup.model_validate(item)
    return None
