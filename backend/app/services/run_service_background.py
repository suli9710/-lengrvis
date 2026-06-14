"""Process-resident asyncio loop and active run task tracking for run_service."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading

from app.core import db

_ACTIVE_RUN_TASKS: dict[str, asyncio.Future | concurrent.futures.Future] = {}
_RESIDENT_LOOP_TASKS: dict[str, asyncio.Task] = {}
_ACTIVE_RUN_TASKS_LOCK = threading.RLock()
_BACKGROUND_LOOP: asyncio.AbstractEventLoop | None = None
_BACKGROUND_LOOP_LOCK = threading.Lock()


def ensure_background_loop() -> asyncio.AbstractEventLoop:
    """Return the process-resident event loop that drives run engine loops."""
    global _BACKGROUND_LOOP
    with _BACKGROUND_LOOP_LOCK:
        loop = _BACKGROUND_LOOP
        if loop is not None and not loop.is_closed():
            return loop
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _drive() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        threading.Thread(target=_drive, name="run-service-engine-loop", daemon=True).start()
        ready.wait()
        _BACKGROUND_LOOP = loop
        return loop


def schedule_background(coro, *, data_dir: str | None = None) -> concurrent.futures.Future:
    async def run_with_data_dir():
        with db.using_data_dir(data_dir):
            return await coro

    return asyncio.run_coroutine_threadsafe(run_with_data_dir(), ensure_background_loop())


def track_active_run(run_id: str, task: asyncio.Future | concurrent.futures.Future) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        _ACTIVE_RUN_TASKS[run_id] = task


def track_active_run_if_idle(run_id: str, task: asyncio.Future | concurrent.futures.Future) -> bool:
    """Atomically claim the active-run slot for ``run_id``.

    Returns False (without tracking) when another, still-running task already
    owns the slot. This closes the resume race where two concurrent callers both
    pass a separate ``run_active`` check and start duplicate engine loops for one
    run, corrupting step state and the active-run tracking.
    """
    with _ACTIVE_RUN_TASKS_LOCK:
        existing = _ACTIVE_RUN_TASKS.get(run_id)
        if existing is not None and not existing.done():
            return False
        _ACTIVE_RUN_TASKS[run_id] = task
        return True


def untrack_active_run(run_id: str) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        _ACTIVE_RUN_TASKS.pop(run_id, None)


def run_active(run_id: str) -> bool:
    with _ACTIVE_RUN_TASKS_LOCK:
        task = _ACTIVE_RUN_TASKS.get(run_id)
    if task is None:
        return False
    return not task.done()


def active_run_ids() -> list[str]:
    with _ACTIVE_RUN_TASKS_LOCK:
        return [run_id for run_id, task in _ACTIVE_RUN_TASKS.items() if not task.done()]


def register_resident_task(run_id: str, task: asyncio.Task) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        _RESIDENT_LOOP_TASKS[run_id] = task


def unregister_resident_task(run_id: str) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        _RESIDENT_LOOP_TASKS.pop(run_id, None)


def leftover_active_tasks() -> dict[str, asyncio.Future | concurrent.futures.Future]:
    with _ACTIVE_RUN_TASKS_LOCK:
        return {run_id: task for run_id, task in _ACTIVE_RUN_TASKS.items() if not task.done()}


def cancel_active_run_task(run_id: str, *, grace_seconds: float = 0.0) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        work = _ACTIVE_RUN_TASKS.get(run_id)
    if work is None or work.done():
        return

    def _hard_cancel() -> None:
        if work.done():
            return
        with _ACTIVE_RUN_TASKS_LOCK:
            resident = _RESIDENT_LOOP_TASKS.get(run_id)
        if resident is not None and not resident.done():
            resident.cancel()
            return
        work.cancel()

    if grace_seconds <= 0:
        _hard_cancel()
        return
    loop = ensure_background_loop()
    loop.call_soon_threadsafe(loop.call_later, grace_seconds, _hard_cancel)
