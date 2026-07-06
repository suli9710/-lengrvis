"""Process-resident asyncio loop and active run task tracking for run_service."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading

from app.core import db

FutureLike = asyncio.Future | concurrent.futures.Future


class ActiveRunHandle(concurrent.futures.Future):
    """Claim token that proxies cancellation/completion to the scheduled work."""

    def __init__(self) -> None:
        super().__init__()
        self._work: FutureLike | None = None
        self._work_lock = threading.RLock()

    def bind(self, work: FutureLike) -> None:
        with self._work_lock:
            if self.done():
                work.cancel()
                return
            self._work = work
        if _future_done(work):
            self._complete_from_work(work)
            return
        add_done_callback = getattr(work, "add_done_callback", None)
        if callable(add_done_callback):
            add_done_callback(self._complete_from_work)

    def cancel(self) -> bool:
        with self._work_lock:
            work = self._work
        if work is not None and not _future_done(work):
            work.cancel()
        return super().cancel()

    def _complete_from_work(self, work: FutureLike) -> None:
        if self.done():
            return
        if _future_cancelled(work):
            super().cancel()
            return
        try:
            self.set_result(None)
        except concurrent.futures.InvalidStateError:
            pass


_ACTIVE_RUN_TASKS: dict[str, FutureLike] = {}
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


def _future_done(task: object) -> bool:
    done = getattr(task, "done", None)
    if callable(done):
        return bool(done())
    return False


def _future_cancelled(task: object) -> bool:
    cancelled = getattr(task, "cancelled", None)
    if callable(cancelled):
        return bool(cancelled())
    return False


def new_active_run_handle() -> ActiveRunHandle:
    return ActiveRunHandle()


def track_active_run(run_id: str, task: FutureLike) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        _ACTIVE_RUN_TASKS[run_id] = task


def track_active_run_if_idle(run_id: str, task: FutureLike) -> bool:
    """Atomically claim the active-run slot for ``run_id``.

    Returns False (without tracking) when another, still-running task already
    owns the slot. This closes the resume race where two concurrent callers both
    pass a separate ``run_active`` check and start duplicate engine loops for one
    run, corrupting step state and the active-run tracking.
    """
    with _ACTIVE_RUN_TASKS_LOCK:
        existing = _ACTIVE_RUN_TASKS.get(run_id)
        if existing is not None and not _future_done(existing):
            return False
        _ACTIVE_RUN_TASKS[run_id] = task
        return True


def bind_active_run(run_id: str, owner: FutureLike, work: FutureLike) -> bool:
    with _ACTIVE_RUN_TASKS_LOCK:
        if _ACTIVE_RUN_TASKS.get(run_id) is not owner:
            return False
    if isinstance(owner, ActiveRunHandle):
        owner.bind(work)
    return True


def active_run_owned_by(run_id: str, owner: FutureLike) -> bool:
    with _ACTIVE_RUN_TASKS_LOCK:
        return _ACTIVE_RUN_TASKS.get(run_id) is owner


def untrack_active_run(run_id: str, owner: FutureLike | None = None) -> bool:
    with _ACTIVE_RUN_TASKS_LOCK:
        if owner is not None and _ACTIVE_RUN_TASKS.get(run_id) is not owner:
            return False
        _ACTIVE_RUN_TASKS.pop(run_id, None)
        return True


def run_active(run_id: str) -> bool:
    with _ACTIVE_RUN_TASKS_LOCK:
        task = _ACTIVE_RUN_TASKS.get(run_id)
    if task is None:
        return False
    return not _future_done(task)


def active_run_ids() -> list[str]:
    with _ACTIVE_RUN_TASKS_LOCK:
        return [run_id for run_id, task in _ACTIVE_RUN_TASKS.items() if not _future_done(task)]


def register_resident_task(run_id: str, task: asyncio.Task, owner: FutureLike | None = None) -> bool:
    with _ACTIVE_RUN_TASKS_LOCK:
        if owner is not None and _ACTIVE_RUN_TASKS.get(run_id) is not owner:
            return False
        _RESIDENT_LOOP_TASKS[run_id] = task
        return True


def unregister_resident_task(run_id: str, task: asyncio.Task | None = None) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        if task is not None and _RESIDENT_LOOP_TASKS.get(run_id) is not task:
            return
        _RESIDENT_LOOP_TASKS.pop(run_id, None)


def leftover_active_tasks() -> dict[str, FutureLike]:
    with _ACTIVE_RUN_TASKS_LOCK:
        return {run_id: task for run_id, task in _ACTIVE_RUN_TASKS.items() if not _future_done(task)}


def cancel_active_run_task(run_id: str, *, grace_seconds: float = 0.0) -> None:
    with _ACTIVE_RUN_TASKS_LOCK:
        work = _ACTIVE_RUN_TASKS.get(run_id)
    if work is None or _future_done(work):
        return

    def _hard_cancel() -> None:
        if _future_done(work):
            return
        with _ACTIVE_RUN_TASKS_LOCK:
            resident = _RESIDENT_LOOP_TASKS.get(run_id)
        if resident is not None and not _future_done(resident):
            resident.cancel()
            return
        work.cancel()

    if grace_seconds <= 0:
        _hard_cancel()
        return
    loop = ensure_background_loop()
    loop.call_soon_threadsafe(loop.call_later, grace_seconds, _hard_cancel)
