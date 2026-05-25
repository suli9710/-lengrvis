"""Tests for P0-5 scheduled task executor.

Run a `Scheduler.tick()` with a fake current time + an injected executor so we
don't spin up an actual orchestrator or wait for cron windows.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core import db
from app.services import scheduler_service
from app.services.scheduler_service import Scheduler, _next_run, _utc_now


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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


def test_tick_skips_not_due_schedules():
    sched = Scheduler(executor=lambda g, m: asyncio.sleep(0))  # type: ignore[arg-type]
    item = sched.schedule("0 9 * * *", "daily 9am", mode="privacy")
    # next_run is at next 9am, so right now should not be due.
    now = _utc_now().replace(hour=8, minute=0, second=0, microsecond=0)

    async def runner():
        return await sched.tick(now=now)

    fired = asyncio.run(runner())
    assert item.id not in fired


def test_next_run_returns_iso_in_utc():
    iso = _next_run("*/5 * * * *")
    parsed = datetime.fromisoformat(iso)
    assert parsed.tzinfo is not None
