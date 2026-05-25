"""Tests for P1-6 multi-task bounded concurrency."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core import db
from app.core.schemas import Task, TaskStatus
from app.services import task_pool


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


def _make_task(idx: int) -> Task:
    return Task(user_goal=f"goal-{idx}", mode="privacy", status=TaskStatus.CREATED)


def test_pool_limits_concurrency():
    pool = task_pool.reset_pool_for_tests(max_concurrent=2)
    started: list[str] = []
    release_signal = asyncio.Event()

    async def runner(task: Task) -> Task:
        started.append(task.id)
        await release_signal.wait()
        return task

    async def main():
        tasks = [_make_task(i) for i in range(5)]
        submitted = [await pool.submit(t, runner) for t in tasks]
        await asyncio.sleep(0.05)
        assert pool.status()["running_count"] == 2
        release_signal.set()
        await asyncio.gather(*submitted)
        return started

    started_ids = asyncio.run(main())
    assert len(started_ids) == 5


def test_pool_cancel_running_task():
    pool = task_pool.reset_pool_for_tests(max_concurrent=2)

    async def runner(task: Task) -> Task:
        await asyncio.sleep(10)
        return task

    async def main():
        task = _make_task(1)
        await pool.submit(task, runner)
        await asyncio.sleep(0.01)
        ok = await pool.cancel(task.id)
        assert ok is True
        assert pool.status()["completed"][task.id] == "cancelled"

    asyncio.run(main())


def test_pool_shutdown_drains_running_tasks():
    pool = task_pool.reset_pool_for_tests(max_concurrent=3)

    async def runner(task: Task) -> Task:
        await asyncio.sleep(0.5)
        return task

    async def main():
        for i in range(3):
            await pool.submit(_make_task(i), runner)
        await asyncio.sleep(0.01)
        assert pool.status()["running_count"] == 3
        await pool.shutdown()
        assert pool.status()["running_count"] == 0

    asyncio.run(main())


def test_pool_records_failure():
    pool = task_pool.reset_pool_for_tests(max_concurrent=2)

    async def runner(task: Task) -> Task:
        raise RuntimeError("boom")

    async def main():
        task = _make_task(99)
        spawned = await pool.submit(task, runner)
        await spawned
        completed = pool.status()["completed"][task.id]
        assert completed.startswith("failed")

    asyncio.run(main())
