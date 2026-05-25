"""TaskPool: bounded concurrency for background OrchestratorAgent runs."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from app.core.audit import record
from app.core.schemas import Task


class TaskPool:
    def __init__(self, max_concurrent: int = 3) -> None:
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queued: dict[str, asyncio.Task] = {}
        self._running: dict[str, asyncio.Task] = {}
        self._completed: dict[str, str] = {}

    async def submit(
        self,
        task: Task,
        runner: Callable[[Task], Awaitable[Task]],
    ) -> asyncio.Task:
        async def _wrap():
            try:
                async with self._semaphore:
                    self._queued.pop(task.id, None)
                    self._running[task.id] = asyncio.current_task()  # type: ignore[assignment]
                    try:
                        await runner(task)
                        self._completed[task.id] = "completed"
                    except asyncio.CancelledError:
                        self._completed[task.id] = "cancelled"
                        raise
                    except Exception as exc:  # noqa: BLE001
                        self._completed[task.id] = f"failed:{exc}"
                        record("task_pool.run_failed", "TaskPool", {"task_id": task.id, "error": str(exc)}, task_id=task.id)
                    finally:
                        self._running.pop(task.id, None)
            except asyncio.CancelledError:
                # task was cancelled while still queued
                self._completed.setdefault(task.id, "cancelled")
                raise
            finally:
                self._queued.pop(task.id, None)

        spawned = asyncio.create_task(_wrap(), name=f"task-{task.id}")
        self._queued[task.id] = spawned
        return spawned

    def status(self) -> dict[str, dict]:
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
        target = self._running.get(task_id) or self._queued.get(task_id)
        if target is None:
            return False
        target.cancel()
        try:
            await target
        except asyncio.CancelledError:
            pass
        return True

    async def shutdown(self) -> None:
        outstanding = list(self._running.values()) + list(self._queued.values())
        for t in outstanding:
            t.cancel()
        await asyncio.gather(*outstanding, return_exceptions=True)
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
