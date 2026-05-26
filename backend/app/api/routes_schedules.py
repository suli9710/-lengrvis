from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.schemas import ScheduledTask
from app.services import scheduler_service


router = APIRouter()


class CreateScheduleRequest(BaseModel):
    cron: str
    goal: str
    mode: str = "efficiency"
    note: str = ""


class EnableScheduleRequest(BaseModel):
    enabled: bool = True


@router.get("/schedules")
def list_schedules() -> list[ScheduledTask]:
    return scheduler_service.get_scheduler().list()


@router.post("/schedules")
def create_schedule(payload: CreateScheduleRequest) -> ScheduledTask:
    try:
        return scheduler_service.get_scheduler().schedule(
            payload.cron,
            payload.goal,
            payload.mode,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str) -> dict:
    ok = scheduler_service.get_scheduler().cancel(schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"ok": True, "id": schedule_id}


@router.post("/schedules/{schedule_id}/enable")
def enable_schedule(schedule_id: str, payload: EnableScheduleRequest) -> ScheduledTask:
    item = scheduler_service.get_scheduler().enable(schedule_id, payload.enabled)
    if item is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return item


@router.get("/schedules/status")
def scheduler_status() -> dict:
    return scheduler_service.status()
