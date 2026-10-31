"""Cross-thread cancellation must run on the owning asyncio loop."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading

import pytest

from app.services import run_service_background as background


@pytest.mark.parametrize("mode", ["resident", "direct", "handle", "cancelled_bind"])
def test_cancel_active_run_wakes_owning_loop(monkeypatch, mode):
    loop = asyncio.new_event_loop()
    started = threading.Event()
    finished = threading.Event()
    holder = {}

    async def worker():
        holder["task"] = asyncio.current_task()
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()

    def drive():
        asyncio.set_event_loop(loop)
        loop.create_task(worker())
        loop.run_forever()
        loop.close()

    def wake_loop():
        # A scheduled callback wakes the selector even when it has no other work.
        started.set()

    thread = threading.Thread(target=drive)
    thread.start()
    try:
        assert started.wait(5)
        task = holder["task"]
        owner = concurrent.futures.Future() if mode == "resident" else task
        if mode in {"handle", "cancelled_bind"}:
            owner = background.ActiveRunHandle()
            if mode == "cancelled_bind":
                owner.cancel()
            owner.bind(task)
        monkeypatch.setattr(background, "_ACTIVE_RUN_TASKS", {"run-cross-thread": owner})
        monkeypatch.setattr(
            background, "_RESIDENT_LOOP_TASKS", {"run-cross-thread": task} if mode == "resident" else {}
        )
        background.cancel_active_run_task("run-cross-thread")
        assert finished.wait(1), "cancellation must wake the resident loop, not wait for unrelated activity"
    finally:
        # Wake an incorrectly cancelled baseline task as well, so failures do not leak a thread.
        loop.call_soon_threadsafe(wake_loop)
        finished.wait(5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)


@pytest.mark.parametrize("close_during_schedule", [False, True])
def test_handle_cancellation_tolerates_closed_owner_loop(monkeypatch, close_during_schedule):
    loop = asyncio.new_event_loop()
    work = loop.create_future()
    owner = background.ActiveRunHandle()
    owner.bind(work)
    if close_during_schedule:

        def close_before_schedule(callback):
            loop.close()
            raise RuntimeError("Event loop is closed")

        monkeypatch.setattr(loop, "call_soon_threadsafe", close_before_schedule)
    else:
        loop.close()
    assert owner.cancel()
    assert owner.cancelled()
    # An unavailable executor cannot run finally blocks. Do not claim that its
    # asyncio work completed, or directly mutate it from this thread.
    assert not work.done()


def test_open_loop_scheduling_failure_is_not_silenced(monkeypatch):
    loop = asyncio.new_event_loop()
    work = loop.create_future()

    def broken_schedule(callback):
        raise RuntimeError("unexpected scheduling failure")

    monkeypatch.setattr(loop, "call_soon_threadsafe", broken_schedule)
    try:
        with pytest.raises(RuntimeError, match="unexpected scheduling failure"):
            background._cancel_future(work)
    finally:
        loop.close()
