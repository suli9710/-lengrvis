from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.schemas import ScheduledTask, now_iso
from app.services import wakeup_service
from app.services.scheduler_service import _next_run


DEFAULT_GUARDIAN_TICK_SECONDS = 30


class GuardianScheduler:
    def __init__(self, *, tick_seconds: int = DEFAULT_GUARDIAN_TICK_SECONDS) -> None:
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        db.init_db()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="mavris-guardian-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None

    async def tick(self, *, now: datetime | None = None) -> list[str]:
        fired: list[str] = []
        now_dt = now or datetime.now(timezone.utc)
        for row in db.fetch_many("scheduled_tasks", "enabled = 1", (), limit=500):
            if not _due(row, now=now_dt):
                continue
            schedule = ScheduledTask.model_validate(row)
            fired.append(schedule.id)
            schedule.last_run_at = now_dt.replace(microsecond=0).isoformat()
            schedule.last_status = "waiting_user_confirmation"
            schedule.next_run_at = _next_run(schedule.cron, base=now_dt)
            schedule.updated_at = now_iso()
            db.upsert_model("scheduled_tasks", schedule)
            wakeup_service.create_schedule_wakeup(schedule, due_at=schedule.last_run_at)
            record(
                "guardian_scheduler.wakeup_created",
                "GuardianScheduler",
                {"schedule_id": schedule.id, "goal": schedule.goal},
                task_id=schedule.id,
            )
        return fired

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001
                record("guardian_scheduler.tick_failed", "GuardianScheduler", {"error": str(exc)})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                continue
            else:
                break

    def status(self) -> dict[str, Any]:
        return {
            "status": "running" if self._task and not self._task.done() else "idle",
            "tick_seconds": self.tick_seconds,
        }


def _due(schedule_data: dict[str, Any], now: datetime) -> bool:
    next_run = schedule_data.get("next_run_at") or ""
    if not next_run:
        return True
    try:
        parsed = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return parsed.astimezone(timezone.utc) <= now


_scheduler: GuardianScheduler | None = None


def get_guardian_scheduler() -> GuardianScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = GuardianScheduler()
    return _scheduler
