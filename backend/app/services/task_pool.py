"""TaskPool: bounded concurrency for background OrchestratorAgent runs."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from contextlib import suppress

from app.core.audit import record
from app.core.schemas import Task
from app.observability.best_effort import log_best_effort_failure

logger = logging.getLogger(__name__)


class TaskPool:
    # Bound on the completed-status history; without it the dict grows for
    # the lifetime of the (long-running desktop) process.
    COMPLETED_HISTORY_LIMIT = 200

    def __init__(self, max_concurrent: int = 3) -> None:
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queued: dict[str, asyncio.Task] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._completed: dict[str, str] = {}
        self._lock = threading.RLock()

    def _record_completed(self, task_id: str, status: str, *, keep_existing: bool = False) -> None:
        with self._lock:
            if keep_existing and task_id in self._completed:
                return
            self._completed.pop(task_id, None)
            self._completed[task_id] = status
            while len(self._completed) > self.COMPLETED_HISTORY_LIMIT:
                self._completed.pop(next(iter(self._completed)))

    def active_task(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._queued or task_id in self._running

    def submit_nowait(
        self,
        task: Task,
        runner: Callable[[Task], Awaitable[Task]],
    ) -> asyncio.Task:
        async def _wrap():
            current = asyncio.current_task()
            try:
                async with self._semaphore:
                    with self._lock:
                        if self._queued.get(task.id) is current:
                            self._queued.pop(task.id, None)
                        if current is not None:
                            self._running[task.id] = current
                    try:
                        await runner(task)
                        self._record_completed(task.id, "completed")
                    except asyncio.CancelledError:
                        self._record_completed(task.id, "cancelled")
                        raise
                    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
                        self._record_completed(task.id, f"failed:{exc}")
                        log_best_effort_failure(logger, "task_pool.worker.run", exc, task_id=task.id)
                        record(
                            "task_pool.run_failed",
                            "TaskPool",
                            {"task_id": task.id, "error": str(exc)},
                            task_id=task.id,
                        )
                    finally:
                        with self._lock:
                            if self._running.get(task.id) is current:
                                self._running.pop(task.id, None)
            except asyncio.CancelledError:
                # task was cancelled while still queued
                self._record_completed(task.id, "cancelled", keep_existing=True)
                raise
            finally:
                with self._lock:
                    if self._queued.get(task.id) is current:
                        self._queued.pop(task.id, None)

        with self._lock:
            existing = self._running.get(task.id) or self._queued.get(task.id)
            if existing is not None and not existing.done():
                record(
                    "task_pool.duplicate_submit_ignored",
                    "TaskPool",
                    {"task_id": task.id, "state": "running" if task.id in self._running else "queued"},
                    task_id=task.id,
                )
                return existing
            spawned = asyncio.create_task(_wrap(), name=f"task-{task.id}")
            self._queued[task.id] = spawned
        return spawned

    async def submit(
        self,
        task: Task,
        runner: Callable[[Task], Awaitable[Task]],
    ) -> asyncio.Task:
        """Compatibility coroutine for callers that await submission.

        New execution boundaries should use :meth:`submit_nowait` so a worker
        is claimed by the bounded pool before another request can pause or
        cancel the task.
        """
        return self.submit_nowait(task, runner)

    def status(self) -> dict[str, dict]:
        with self._lock:
            return {
                "max_concurrent": self.max_concurrent,
                "running": list(self._running.keys()),
                "running_count": len(self._running),
                "queued": list(self._queued.keys()),
                "queued_count": len(self._queued),
                "available_slots": max(0, self.max_concurrent - len(self._running)),
                "completed": dict(self._completed),
            }

    async def cancel(self, task_id: str) -> bool:
        with self._lock:
            target = self._running.get(task_id) or self._queued.get(task_id)
        if target is None:
            return False
        target.cancel()
        with suppress(asyncio.CancelledError):
            await target
        # A task cancelled before its coroutine gets its first event-loop turn
        # never enters _wrap's finally block. Release the synchronous claim
        # here as well so pause/cancel cannot leave a ghost active entry.
        with self._lock:
            if self._running.get(task_id) is target:
                self._running.pop(task_id, None)
            if self._queued.get(task_id) is target:
                self._queued.pop(task_id, None)
        self._record_completed(task_id, "cancelled", keep_existing=True)
        return True

    async def shutdown(self) -> None:
        with self._lock:
            outstanding = list(self._running.values()) + list(self._queued.values())
        for t in outstanding:
            t.cancel()
        await asyncio.gather(*outstanding, return_exceptions=True)
        with self._lock:
            self._running.clear()
            self._queued.clear()


_pool: TaskPool | None = None


def get_pool() -> TaskPool:
    global _pool
    if _pool is None:
        _pool = TaskPool()
    return _pool


def reset_pool_for_tests(max_concurrent: int = 3) -> TaskPool:
    """Test helper to install a fresh pool."""
    global _pool
    _pool = TaskPool(max_concurrent=max_concurrent)
    return _pool
