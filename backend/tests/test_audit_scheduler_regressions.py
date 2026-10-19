from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import ScheduledTask, Task
from app.main import create_app
from app.services import notification_service, scheduler_service
from app.services.scheduler_service import Scheduler


@pytest.fixture(autouse=True)
def isolated_scheduler(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PLAN", "pro")
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "true")
    scheduler_service._scheduler = None
    db.init_db()
    yield
    scheduler_service._scheduler = None


@pytest.mark.parametrize(
    "status", ["failed", "denied", "cancelled", "repair_required", "waiting_user_approval", "paused", "completed"]
)
def test_real_task_outcome_controls_schedule_and_notification(monkeypatch, status):
    from app.agents.orchestrator_agent import OrchestratorAgent

    task = Task(user_goal="scheduled work", status=status)
    db.upsert_model("tasks", task)

    async def execute(self, goal, mode):
        return task

    # Orchestrator is the scheduler's execution boundary. Its public Task
    # result can represent a failure or a parked workflow without raising.
    monkeypatch.setattr(OrchestratorAgent, "handle_user_goal", execute)
    notifications = []
    monkeypatch.setattr(notification_service, "notify", lambda title, *a, **kw: notifications.append(title))
    scheduler = Scheduler()
    item = scheduler.schedule("*/5 * * * *", "scheduled work")
    due = datetime.fromisoformat(item.next_run_at)

    async def run():
        assert await scheduler.tick(now=due) == [item.id]
        await asyncio.gather(*scheduler._executions)

    asyncio.run(run())
    expected = "awaiting_approval" if status == "waiting_user_approval" else status
    stored = scheduler.get(item.id)
    assert stored.last_status == expected
    assert stored.last_task_id == task.id
    assert ("定时任务完成" in notifications) == (status == "completed")


def test_approval_completion_is_reconciled_even_when_schedule_is_disabled(monkeypatch):
    task = Task(user_goal="pending work", status="waiting_user_approval")
    db.upsert_model("tasks", task)
    item = ScheduledTask(
        cron="0 9 * * *",
        goal=task.user_goal,
        enabled=False,
        last_status="awaiting_approval",
        last_task_id=task.id,
        last_run_at="2026-09-05T01:00:00+00:00",
        next_run_at="2026-09-06T01:00:00+00:00",
    )
    db.upsert_model("scheduled_tasks", item)
    task = Task(id=task.id, user_goal=task.user_goal, status="completed")
    db.upsert_model("tasks", task)
    notifications = []
    monkeypatch.setattr(notification_service, "notify", lambda title, *a, **kw: notifications.append(title))
    scheduler = Scheduler()
    asyncio.run(scheduler.tick(now=datetime(2026, 9, 5, 2, tzinfo=UTC)))
    asyncio.run(scheduler.tick(now=datetime(2026, 9, 5, 2, tzinfo=UTC)))
    assert scheduler.get(item.id).last_status == "completed"
    assert notifications == ["定时任务完成"]


def test_delete_api_removes_schedule_and_cannot_reenable():
    scheduler = scheduler_service.get_scheduler()
    item = scheduler.schedule("0 9 * * *", "delete me")
    client = TestClient(create_app())
    assert client.delete(f"/api/schedules/{item.id}").status_code == 200
    assert client.get("/api/schedules").json() == []
    assert client.post(f"/api/schedules/{item.id}/enable", json={"enabled": True}).status_code == 404


def test_delete_during_execution_does_not_restore_or_notify(monkeypatch):
    notifications = []
    monkeypatch.setattr(notification_service, "notify", lambda *a, **kw: notifications.append(a))
    scheduler = scheduler_service.get_scheduler()
    item = scheduler.schedule("*/5 * * * *", "delete while running")

    async def executor(goal, mode):
        assert TestClient(create_app()).delete(f"/api/schedules/{item.id}").status_code == 200
        return "task-finished"

    scheduler._executor = executor

    async def run():
        await scheduler.tick(now=datetime.fromisoformat(item.next_run_at))
        await asyncio.gather(*scheduler._executions)

    asyncio.run(run())
    assert scheduler.get(item.id) is None
    assert notifications == []


@pytest.mark.parametrize("outcome", ["failed", "completed", "waiting_user_approval"])
def test_execution_result_survives_pause_and_later_reenable(monkeypatch, outcome):
    clock = [datetime(2026, 9, 5, tzinfo=UTC)]
    monkeypatch.setattr(scheduler_service, "_utc_now", lambda: clock[0])
    notifications = []
    monkeypatch.setattr(notification_service, "notify", lambda title, *a, **kw: notifications.append(title))
    task = Task(user_goal="pause and resume schedule", status=outcome)
    db.upsert_model("tasks", task)
    scheduler = Scheduler()
    item = scheduler.schedule("* * * * *", task.user_goal)

    async def executor(goal, mode):
        scheduler.enable(item.id, False)
        clock[0] = datetime(2026, 9, 5, 0, 3, tzinfo=UTC)
        scheduler.enable(item.id, True)
        return task

    scheduler._executor = executor

    async def run():
        assert await scheduler.tick(now=datetime.fromisoformat(item.next_run_at)) == [item.id]
        await asyncio.gather(*scheduler._executions)

    asyncio.run(run())
    stored = scheduler.get(item.id)
    assert stored.last_status == ("awaiting_approval" if outcome == "waiting_user_approval" else outcome)
    assert stored.last_task_id == task.id
    assert stored.enabled is True
    assert stored.next_run_at == "2026-09-05T00:04:00+00:00"
    assert len(notifications) == (0 if outcome == "waiting_user_approval" else 1)


def test_older_execution_cannot_overwrite_newer_claim(monkeypatch):
    monkeypatch.setattr(scheduler_service, "_utc_now", lambda: datetime(2026, 9, 5, tzinfo=UTC))
    notifications = []
    monkeypatch.setattr(notification_service, "notify", lambda title, *a, **kw: notifications.append(title))
    scheduler = Scheduler()
    item = scheduler.schedule("* * * * *", "two overlapping runs")
    old_task = Task(user_goal=item.goal, status="failed")
    new_task = Task(user_goal=item.goal, status="completed")

    async def run():
        old_started, release_old = asyncio.Event(), asyncio.Event()
        calls = 0

        async def executor(goal, mode):
            nonlocal calls
            calls += 1
            if calls == 1:
                old_started.set()
                await release_old.wait()
                return old_task
            return new_task

        scheduler._executor = executor
        await scheduler.tick(now=datetime(2026, 9, 5, 0, 1, tzinfo=UTC))
        await old_started.wait()
        old_execution = next(iter(scheduler._executions))
        await scheduler.tick(now=datetime(2026, 9, 5, 0, 2, tzinfo=UTC))
        await asyncio.gather(*(execution for execution in scheduler._executions if execution is not old_execution))
        release_old.set()
        await old_execution

    asyncio.run(run())
    stored = scheduler.get(item.id)
    assert stored.last_run_at == "2026-09-05T00:02:00+00:00"
    assert stored.last_status == "completed"
    assert stored.last_task_id == new_task.id
    assert notifications == ["定时任务完成"]


def test_schedule_api_persists_local_timezone_and_reuses_it_when_enabled(monkeypatch):
    monkeypatch.setattr(scheduler_service, "_utc_now", lambda: datetime(2026, 9, 5, tzinfo=UTC))
    client = TestClient(create_app())
    response = client.post("/api/schedules", json={"cron": "0 9 * * *", "goal": "morning", "timezone": "Asia/Taipei"})
    assert response.status_code == 200
    item = response.json()
    assert item.get("timezone") == "Asia/Taipei"
    assert item["next_run_at"] == "2026-09-05T01:00:00+00:00"
    client.post(f"/api/schedules/{item['id']}/enable", json={"enabled": False})
    refreshed = client.post(f"/api/schedules/{item['id']}/enable", json={"enabled": True}).json()
    assert refreshed["next_run_at"] == item["next_run_at"]
    invalid = client.post("/api/schedules", json={"cron": "0 9 * * *", "goal": "x", "timezone": "Invalid/Zone"})
    assert invalid.status_code in {400, 422}


def test_legacy_schedules_remain_utc(monkeypatch):
    monkeypatch.setattr(scheduler_service, "_utc_now", lambda: datetime(2026, 9, 5, tzinfo=UTC))
    item = ScheduledTask.model_validate({"cron": "0 9 * * *", "goal": "old task"})
    db.upsert_model("scheduled_tasks", item)
    assert Scheduler().enable(item.id, True).next_run_at == "2026-09-05T09:00:00+00:00"


def test_cron_respects_dst_for_daily_morning():
    before = datetime(2026, 3, 7, 15, tzinfo=UTC)
    after = scheduler_service._next_run("0 9 * * *", base=before, timezone="America/New_York")
    assert after == "2026-03-08T13:00:00+00:00"
