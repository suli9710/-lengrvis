"""Tests for P0-5 scheduled task executor.

Run a `Scheduler.tick()` with a fake current time + an injected executor so we
don't spin up an actual orchestrator or wait for cron windows.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.core import db
from app.services import scheduler_service
from app.services.scheduler_service import Scheduler, _next_run, _utc_now


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    # Reset singleton
    scheduler_service._scheduler = None
    db.init_db()
    yield
    scheduler_service._scheduler = None


def test_schedule_persists_with_next_run():
    sched = Scheduler()
    item = sched.schedule("*/5 * * * *", "整理桌面", mode="privacy", note="demo")
    assert item.cron == "*/5 * * * *"
    assert item.goal == "整理桌面"
    assert item.next_run_at != ""
    rehydrated = sched.get(item.id)
    assert rehydrated is not None
    assert rehydrated.enabled is True


def test_invalid_cron_raises():
    sched = Scheduler()
    with pytest.raises(ValueError):
        sched.schedule("not a cron", "x", "privacy")


def test_cancel_disables_schedule():
    sched = Scheduler()
    item = sched.schedule("*/5 * * * *", "x", "privacy")
    assert sched.cancel(item.id) is True

    refreshed = sched.get(item.id)
    assert refreshed is not None
    assert refreshed.enabled is False


def test_enable_recomputes_next_run():
    sched = Scheduler()
    item = sched.schedule("*/5 * * * *", "x", "privacy")
    sched.cancel(item.id)
    re_enabled = sched.enable(item.id, True)
    assert re_enabled is not None
    assert re_enabled.enabled is True
    assert re_enabled.next_run_at != ""


def test_tick_fires_due_schedules_through_injected_executor():
    captured: list[tuple[str, str]] = []

    async def executor(goal: str, mode: str) -> str:
        captured.append((goal, mode))
        return f"task-{len(captured)}"

    sched = Scheduler(executor=executor)
    item = sched.schedule("*/5 * * * *", "scan downloads", mode="hybrid")

    # Pretend the clock is well past the next_run.
    far_future = _utc_now() + timedelta(days=1)

    async def runner():
        fired = await sched.tick(now=far_future)
        # Give the spawned _execute task a chance to finish.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return fired

    fired_ids = asyncio.run(runner())
    assert item.id in fired_ids
    assert captured == [("scan downloads", "hybrid")]
    refreshed = sched.get(item.id)
    assert refreshed is not None
    assert refreshed.last_status == "completed"
    assert refreshed.last_task_id == "task-1"


def test_scheduler_execute_failure_logs_best_effort_warning(caplog):
    async def executor(goal: str, mode: str) -> str:  # noqa: ARG001
        raise RuntimeError("executor exploded")

    sched = Scheduler(executor=executor)
    item = sched.schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    far_future = _utc_now() + timedelta(days=1)

    async def runner():
        fired = await sched.tick(now=far_future)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return fired

    with caplog.at_level(logging.WARNING, logger=scheduler_service.logger.name):
        fired_ids = asyncio.run(runner())

    assert item.id in fired_ids
    refreshed = sched.get(item.id)
    assert refreshed is not None
    assert refreshed.last_status.startswith("failed:")
    assert "scheduler.execute" in caplog.text
    assert "executor exploded" in caplog.text


def test_scheduler_execute_failure_persists_redacted_status():
    private_path = "C:/Users/Suli/private/schedules/.env"
    private_file = "scheduler-output.log"
    secret_token = "scheduler-secret-1234567890"

    async def executor(goal: str, mode: str) -> str:  # noqa: ARG001
        raise RuntimeError(f"executor failed at {private_path} {private_file} token={secret_token}")

    sched = Scheduler(executor=executor)
    item = sched.schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    far_future = _utc_now() + timedelta(days=1)

    async def runner():
        fired = await sched.tick(now=far_future)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return fired

    fired_ids = asyncio.run(runner())

    assert item.id in fired_ids
    refreshed = sched.get(item.id)
    assert refreshed is not None
    assert refreshed.last_status.startswith("failed: executor failed")
    assert "[REDACTED_LOCAL_PATH]" in refreshed.last_status
    assert "[REDACTED_FILE_NAME]" in refreshed.last_status
    assert private_path not in refreshed.last_status
    assert private_file not in refreshed.last_status
    assert secret_token not in refreshed.last_status
    status_text = str(sched.status())
    assert private_path not in status_text
    assert private_file not in status_text
    assert secret_token not in status_text


def test_tick_skips_not_due_schedules():
    sched = Scheduler(executor=lambda g, m: asyncio.sleep(0))  # type: ignore[arg-type]
    item = sched.schedule("0 9 * * *", "daily 9am", mode="privacy")
    # next_run is at next 9am, so right now should not be due.
    now = _utc_now().replace(hour=8, minute=0, second=0, microsecond=0)

    async def runner():
        return await sched.tick(now=now)

    fired = asyncio.run(runner())
    assert item.id not in fired


def test_concurrent_ticks_claim_due_schedule_once(monkeypatch):
    captured: list[tuple[str, str]] = []
    captured_lock = threading.Lock()

    async def executor(goal: str, mode: str) -> str:
        with captured_lock:
            captured.append((goal, mode))
            return f"task-{len(captured)}"

    sched_a = Scheduler(executor=executor)
    sched_b = Scheduler(executor=executor)
    item = sched_a.schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    far_future = _utc_now() + timedelta(days=1)
    original_fetch_many = db.fetch_many
    barrier = threading.Barrier(2)

    def synchronized_fetch_many(table: str, where: str = "", args: tuple = (), limit: int = 200):
        rows = original_fetch_many(table, where, args, limit)
        if table == "scheduled_tasks" and where == "enabled = 1":
            barrier.wait(timeout=5)
        return rows

    monkeypatch.setattr(db, "fetch_many", synchronized_fetch_many)

    results: list[list[str]] = []
    errors: list[BaseException] = []
    results_lock = threading.Lock()

    def run_tick(sched: Scheduler) -> None:
        async def runner() -> list[str]:
            fired = await sched.tick(now=far_future)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return fired

        try:
            fired = asyncio.run(runner())
            with results_lock:
                results.append(fired)
        except BaseException as exc:  # noqa: BLE001
            with results_lock:
                errors.append(exc)

    thread_a = threading.Thread(target=run_tick, args=(sched_a,))
    thread_b = threading.Thread(target=run_tick, args=(sched_b,))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert not thread_a.is_alive()
    assert not thread_b.is_alive()
    assert errors == []
    fired_ids = [schedule_id for fired in results for schedule_id in fired]
    assert fired_ids == [item.id]
    assert captured == [("scan downloads", "hybrid")]


def test_execution_completion_does_not_reenable_cancelled_schedule():
    sched: Scheduler
    item_id = ""

    async def executor(goal: str, mode: str) -> str:  # noqa: ARG001
        assert sched.cancel(item_id) is True
        return "task-1"

    sched = Scheduler(executor=executor)
    item = sched.schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    item_id = item.id
    far_future = _utc_now() + timedelta(days=1)

    async def runner():
        fired = await sched.tick(now=far_future)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return fired

    fired_ids = asyncio.run(runner())
    assert item.id in fired_ids
    refreshed = sched.get(item.id)
    assert refreshed is not None
    assert refreshed.enabled is False
    assert refreshed.last_status == "completed"
    assert refreshed.last_task_id == "task-1"


def test_cancel_preserves_completed_run_metadata():
    captured_schedule: dict[str, str] = {}
    unblock = asyncio.Event()

    async def executor(goal: str, mode: str) -> str:  # noqa: ARG001
        await unblock.wait()
        return "task-1"

    sched = Scheduler(executor=executor)
    item = sched.schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    far_future = _utc_now() + timedelta(days=1)

    async def runner():
        fired = await sched.tick(now=far_future)
        await asyncio.sleep(0)
        running = sched.get(item.id)
        assert running is not None
        captured_schedule["last_run_at"] = running.last_run_at
        captured_schedule["next_run_at"] = running.next_run_at
        assert sched.cancel(item.id) is True
        unblock.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return fired

    fired_ids = asyncio.run(runner())
    assert item.id in fired_ids
    refreshed = sched.get(item.id)
    assert refreshed is not None
    assert refreshed.enabled is False
    assert refreshed.last_run_at == captured_schedule["last_run_at"]
    assert refreshed.next_run_at == captured_schedule["next_run_at"]
    assert refreshed.last_status == "completed"
    assert refreshed.last_task_id == "task-1"


def test_enable_preserves_existing_run_metadata():
    sched = Scheduler()
    item = sched.schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    last_run_at = _utc_now().replace(microsecond=0).isoformat()
    item.last_run_at = last_run_at
    item.last_status = "completed"
    item.last_task_id = "task-1"
    item.enabled = False
    db.upsert_model("scheduled_tasks", item)

    re_enabled = sched.enable(item.id, True)

    assert re_enabled is not None
    assert re_enabled.enabled is True
    assert re_enabled.last_run_at == last_run_at
    assert re_enabled.last_status == "completed"
    assert re_enabled.last_task_id == "task-1"
    assert re_enabled.next_run_at != ""


def test_scheduler_singleton_can_restart_across_event_loops():
    sched = Scheduler(tick_seconds=60, executor=lambda g, m: asyncio.sleep(0))  # type: ignore[arg-type]

    async def run_once():
        await sched.start()
        await sched.stop()

    asyncio.run(run_once())
    asyncio.run(run_once())

    assert sched.status()["status"] == "idle"


def test_next_run_returns_iso_in_utc():
    iso = _next_run("*/5 * * * *")
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None
