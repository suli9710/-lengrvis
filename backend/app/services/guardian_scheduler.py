from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.schemas import ScheduledTask, now_iso
from app.observability.best_effort import log_best_effort_failure
from app.services import wakeup_service
from app.services.scheduler_service import _next_run

DEFAULT_GUARDIAN_TICK_SECONDS = 30
logger = logging.getLogger(__name__)


class GuardianScheduler:
    def __init__(
        self,
        *,
        tick_seconds: int = DEFAULT_GUARDIAN_TICK_SECONDS,
        full_backend_available: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop: asyncio.Event | None = None
        self._full_backend_available = full_backend_available or self._default_full_backend_available

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        db.init_db()
        self._stop = asyncio.Event()
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="lengrvis-guardian-scheduler")

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

    async def tick(self, *, now: datetime | None = None) -> list[str]:
        if await self._full_backend_available():
            return []

        fired: list[str] = []
        now_dt = now or datetime.now(UTC)
        for row in db.fetch_many("scheduled_tasks", "enabled = 1", (), limit=500):
            if not _due(row, now=now_dt):
                continue
            schedule = ScheduledTask.model_validate(row)
            schedule.last_run_at = now_dt.replace(microsecond=0).isoformat()
            schedule.last_status = "waiting_user_confirmation"
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
            wakeup_service.create_schedule_wakeup(schedule, due_at=schedule.last_run_at)
            record(
                "guardian_scheduler.wakeup_created",
                "GuardianScheduler",
                {"schedule_id": schedule.id, "goal": schedule.goal},
                task_id=schedule.id,
            )
        return fired

    async def _run(self) -> None:
        if self._stop is None:
            self._stop = asyncio.Event()
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
                log_best_effort_failure(logger, "guardian_scheduler.run.tick", exc)
                record("guardian_scheduler.tick_failed", "GuardianScheduler", {"error": str(exc)})
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.tick_seconds)
            except TimeoutError:
                continue
            else:
                break

    def status(self) -> dict[str, Any]:
        return {
            "status": "running" if self._task and not self._task.done() else "idle",
            "tick_seconds": self.tick_seconds,
        }

    async def _default_full_backend_available(self) -> bool:
        try:
            from app.services.guardian_runtime import runtime

            return await runtime._is_full_backend_healthy()
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: guardian mode should take over when health checks fail.
            log_best_effort_failure(logger, "guardian_scheduler.full_backend_health", exc)
            return False


def _due(schedule_data: dict[str, Any], now: datetime) -> bool:
    next_run = schedule_data.get("next_run_at") or ""
    if not next_run:
        return True
    try:
        parsed = datetime.fromisoformat(next_run.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    except ValueError:
        return True
    return parsed.astimezone(UTC) <= now


_scheduler: GuardianScheduler | None = None


def get_guardian_scheduler() -> GuardianScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = GuardianScheduler()
    return _scheduler
