from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.schemas import ScheduledTask, now_iso
from app.observability.best_effort import log_best_effort_failure
from app.policy.redaction import redact_public_text, redact_value

try:
    from croniter import croniter as _Croniter

    _CRONITER_AVAILABLE = True
except ImportError:  # pragma: no cover - guarded fallback
    _CRONITER_AVAILABLE = False


_DEFAULT_TICK_SECONDS = 30
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _next_run(cron_expr: str, *, base: datetime | None = None) -> str:
    if not _CRONITER_AVAILABLE:
        # Minimal fallback: assume 5-minute fixed interval when croniter missing.
        # The next run must be strictly in the future, otherwise the schedule
        # stays perpetually "due" and re-fires on every tick (wakeup storm).
        ref = base or _utc_now()
        return (ref + timedelta(minutes=5)).replace(microsecond=0).isoformat()
    ref = base or _utc_now()
    itr = _Croniter(cron_expr, ref)
    next_dt = itr.get_next(datetime)
    if next_dt.tzinfo is None:
        next_dt = next_dt.replace(tzinfo=UTC)
    return next_dt.astimezone(UTC).isoformat()


def _due(schedule_data: dict[str, Any], now: datetime | None = None) -> bool:
    next_run = schedule_data.get("next_run_at") or ""
    if not next_run:
        return True
    try:
        parsed = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    except ValueError:
        return True
    return parsed.astimezone(UTC) <= (now or _utc_now())


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
        self._stop: asyncio.Event | None = None
        self._executor = executor  # async callable (goal, mode) -> None; injected for tests
        self._fired_ids: set[str] = set()
        # Holds strong refs to in-flight executions (loop keeps only weak refs).
        self._executions: set[asyncio.Task] = set()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        db.init_db()
        self._stop = asyncio.Event()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="lengrvis-scheduler")

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None
        self._stop = None
        pending = list(self._executions)
        if pending:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True),
                    timeout=30,
                )
            except TimeoutError:
                for execution in pending:
                    if not execution.done():
                        execution.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

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
        updated = db.set_scheduled_task_enabled(schedule_id, False, updated_at=now_iso())
        if updated is None:
            return False
        record("scheduler.cancelled", "Scheduler", {"id": schedule_id}, task_id=schedule_id)
        return True

    def enable(self, schedule_id: str, enabled: bool) -> ScheduledTask | None:
        item = self.get(schedule_id)
        if not item:
            return None
        next_run_at = item.next_run_at
        if enabled and _CRONITER_AVAILABLE:
            next_run_at = _next_run(item.cron)
        updated = db.set_scheduled_task_enabled(
            schedule_id,
            enabled,
            next_run_at=next_run_at,
            updated_at=now_iso(),
        )
        return ScheduledTask.model_validate(updated) if updated is not None else None

    async def _run(self) -> None:
        if self._stop is None:
            self._stop = asyncio.Event()
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
                log_best_effort_failure(logger, "scheduler.run.tick", exc)
                record("scheduler.tick_failed", "Scheduler", {"error": _safe_scheduler_error(exc)})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except TimeoutError:
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
            schedule.last_run_at = now_dt.replace(microsecond=0).isoformat()
            schedule.last_status = "running"
            # Always advance next_run_at. When croniter is unavailable, _next_run
            # returns the +5min fallback; leaving it unchanged would keep the row
            # perpetually due and re-fire it on every tick.
            schedule.next_run_at = _next_run(schedule.cron, base=now_dt)
            schedule.updated_at = now_iso()
            claimed = db.claim_scheduled_task_run(
                schedule.id,
                expected_next_run_at=str(row.get("next_run_at") or ""),
                claimed_data=schedule.model_dump(mode="json"),
            )
            if claimed is None:
                continue
            schedule = ScheduledTask.model_validate(claimed)
            fired.append(schedule.id)
            self._fired_ids.add(schedule.id)
            execution = asyncio.create_task(self._execute(schedule))
            self._executions.add(execution)
            execution.add_done_callback(self._executions.discard)
        return fired

    async def _execute(self, schedule: ScheduledTask) -> None:
        last_status = "completed"
        last_task_id = ""
        try:
            if self._executor is not None:
                task_id = await self._executor(schedule.goal, schedule.mode)
            else:
                from app.agents.orchestrator_agent import OrchestratorAgent

                orchestrator = OrchestratorAgent()
                task = await orchestrator.handle_user_goal(schedule.goal, schedule.mode)
                task_id = task.id
            last_task_id = str(task_id or "")
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            safe_error = _safe_scheduler_error(exc)
            last_status = f"failed: {safe_error}"
            log_best_effort_failure(logger, "scheduler.execute", exc, schedule_id=schedule.id)
            record(
                "scheduler.execute_failed",
                "Scheduler",
                {"id": schedule.id, "error": safe_error},
                task_id=schedule.id,
            )
        finally:
            persisted = db.complete_scheduled_task_run(
                schedule.id,
                expected_last_run_at=schedule.last_run_at,
                expected_next_run_at=schedule.next_run_at,
                last_status=last_status,
                last_task_id=last_task_id,
            )
            if persisted is not None:
                schedule = ScheduledTask.model_validate(persisted)
            else:
                schedule.last_status = last_status
                schedule.last_task_id = last_task_id

        try:
            from app.services import notification_service

            ok = "failed" not in (schedule.last_status or "").lower()
            notification_service.notify(
                f"定时任务{'完成' if ok else '失败'}",
                schedule.goal or schedule.id,
                task_id=schedule.id,
                severity="info" if ok else "error",
            )
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: notifications are best-effort after schedule persistence.
            log_best_effort_failure(logger, "scheduler.notification", exc, schedule_id=schedule.id)
            record(
                "scheduler.notification_failed",
                "Scheduler",
                {"id": schedule.id, "error": _safe_scheduler_error(exc)},
                task_id=schedule.id,
            )

    def status(self) -> dict[str, Any]:
        return {
            "status": "running" if self._task and not self._task.done() else "idle",
            "tick_seconds": self.tick_seconds,
            "schedules": [item.model_dump() for item in self.list()],
            "cron_engine": "croniter" if _CRONITER_AVAILABLE else "fallback",
        }


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = Scheduler()
    return _scheduler


def status() -> dict[str, Any]:
    return get_scheduler().status()


def _safe_scheduler_error(value: Any) -> str:
    return redact_public_text(str(redact_value(str(value or "")) or "")) or value.__class__.__name__
