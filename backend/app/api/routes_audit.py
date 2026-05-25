from __future__ import annotations

from fastapi import APIRouter

from app.core import db


router = APIRouter()


@router.get("/audit")
def audit():
    return db.fetch_many("audit_events", limit=500)


@router.get("/audit/{task_id}")
def audit_for_task(task_id: str):
    return db.fetch_many("audit_events", "task_id = ?", (task_id,), limit=500)

