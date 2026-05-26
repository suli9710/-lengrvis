from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.schemas import ScheduledTask, now_iso

try:
    from croniter import croniter as _Croniter
    _CRONITER_AVAILABLE = True
except Exception:  # pragma: no cover - guarded fallback
    _CRONITER_AVAILABLE = False


_DEFAULT_TICK_SECONDS = 30


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _next_run(cron_expr: str, *, base: datetime | None = None) -> str:
    if not _CRONITER_AVAILABLE:
        # Minimal fallback: assume 5-minute fixed interval when croniter missing.
        ref = base or _utc_now()
        return ref.replace(microsecond=0).isoformat()
    ref = base or _utc_now()
    itr = _Croniter(cron_expr, ref)
    next_dt = itr.get_next(datetime)
    if next_dt.tzinfo is None:
        next_dt = next_dt.replace(tzinfo=timezone.utc)
    return next_dt.astimezone(timezone.utc).isoformat()


def _due(schedule_data: dict[str, Any], now: datetime | None = None) -> bool:
    next_run = schedule_data.get("next_run_at") or ""
    if not next_run:
        return True
    try:
        parsed = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return parsed.astimezone(timezone.utc) <= (now or _utc_now())


class Scheduler:
    """Single-process async scheduler that fires ScheduledTasks via the orchestrator."""

    def __init__(
        self,
        *,
        tick_seconds: int = _DEFAULT_TICK_SECONDS,
        executor=None,
    ) -> None:
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._executor = executor  # async callable (goal, mode) -> None; injected for tests
        self._fired_ids: set[str] = set()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        db.init_db()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="mavris-scheduler")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None

    def schedule(self, cron: str, goal: str, mode: str = "efficiency", *, note: str = "") -> ScheduledTask:
        if not _CRONITER_AVAILABLE:
            raise RuntimeError("croniter is not installed; scheduling is disabled.")
        if not _Croniter.is_valid(cron):
            raise ValueError(f"Invalid cron expression: {cron}")
        item = ScheduledTask(
            cron=cron,
            goal=goal,
            mode=mode,
            note=note,
            next_run_at=_next_run(cron),
        )
        db.upsert_model("scheduled_tasks", item)
        record("scheduler.created", "Scheduler", {"cron": cron, "goal": goal, "mode": mode}, task_id=item.id)
        return item

    def list(self) -> list[ScheduledTask]:
        rows = db.fetch_many("scheduled_tasks", limit=500)
        return [ScheduledTask.model_validate(row) for row in rows]

    def get(self, schedule_id: str) -> ScheduledTask | None:
        row = db.fetch_one("scheduled_tasks", schedule_id)
        return ScheduledTask.model_validate(row) if row else None

    def cancel(self, schedule_id: str) -> bool:
        item = self.get(schedule_id)
        if not item:
            return False
        item.enabled = False
        item.updated_at = now_iso()
        db.upsert_model("scheduled_tasks", item)
        record("scheduler.cancelled", "Scheduler", {"id": schedule_id}, task_id=schedule_id)
        return True

    def enable(self, schedule_id: str, enabled: bool) -> ScheduledTask | None:
        item = self.get(schedule_id)
        if not item:
            return None
        item.enabled = enabled
        item.updated_at = now_iso()
        if enabled and _CRONITER_AVAILABLE:
            item.next_run_at = _next_run(item.cron)
        db.upsert_model("scheduled_tasks", item)
        return item

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001
                record("scheduler.tick_failed", "Scheduler", {"error": str(exc)})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except asyncio.TimeoutError:
                continue
            else:
                break

    async def tick(self, *, now: datetime | None = None) -> list[str]:
        """Run one tick. Returns list of schedule ids that fired."""
        fired: list[str] = []
        now_dt = now or _utc_now()
        for row in db.fetch_many("scheduled_tasks", "enabled = 1", (), limit=500):
            if not _due(row, now=now_dt):
                continue
            schedule = ScheduledTask.model_validate(row)
            fired.append(schedule.id)
            self._fired_ids.add(schedule.id)
            schedule.last_run_at = now_dt.replace(microsecond=0).isoformat()
            schedule.last_status = "running"
            if _CRONITER_AVAILABLE:
                schedule.next_run_at = _next_run(schedule.cron, base=now_dt)
            db.upsert_model("scheduled_tasks", schedule)
            asyncio.create_task(self._execute(schedule))
        return fired

    async def _execute(self, schedule: ScheduledTask) -> None:
        try:
            if self._executor is not None:
                task_id = await self._executor(schedule.goal, schedule.mode)
            else:
                from app.agents.orchestrator_agent import OrchestratorAgent

                orchestrator = OrchestratorAgent()
                task = await orchestrator.handle_user_goal(schedule.goal, schedule.mode)
                task_id = task.id
            schedule.last_task_id = str(task_id or "")
            schedule.last_status = "completed"
        except Exception as exc:  # noqa: BLE001
            schedule.last_status = f"failed: {exc}"
            record("scheduler.execute_failed", "Scheduler", {"id": schedule.id, "error": str(exc)}, task_id=schedule.id)
        finally:
            db.upsert_model("scheduled_tasks", schedule)

        try:
            from app.services import notification_service

            ok = "failed" not in (schedule.last_status or "").lower()
            notification_service.notify(
                f"定时任务{'完成' if ok else '失败'}",
                schedule.goal or schedule.id,
                task_id=schedule.id,
                severity="info" if ok else "error",
            )
        except Exception:
            pass  # Notification failure should never break scheduler


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


def status() -> dict[str, Any]:
    sched = get_scheduler()
    return {
        "status": "running" if sched._task and not sched._task.done() else "idle",
        "tick_seconds": sched.tick_seconds,
        "schedules": [item.model_dump() for item in sched.list()],
        "cron_engine": "croniter" if _CRONITER_AVAILABLE else "fallback",
    }
