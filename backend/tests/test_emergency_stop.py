from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_runtime
from app.core.schemas import Task
from app.orchestration.task_phase import TaskPhase
from app.services import task_service


def _task(task_id: str, status: TaskPhase) -> Task:
    return Task(id=task_id, user_goal=f"goal-{task_id}", mode="efficiency", status=status)


def test_emergency_stop_cancels_every_nonterminal_task_and_preserves_history(monkeypatch) -> None:
    tasks = [
        _task("created", TaskPhase.CREATED),
        _task("running", TaskPhase.EXECUTION),
        _task("done", TaskPhase.COMPLETED),
        _task("already-cancelled", TaskPhase.CANCELLED),
    ]
    cancelled: list[str] = []

    async def fake_cancel(task_id: str, *, strict=None):  # noqa: ANN001
        assert strict is False
        cancelled.append(task_id)
        return _task(task_id, TaskPhase.CANCELLED)

    monkeypatch.setattr(task_service, "list_tasks", lambda: tasks)
    monkeypatch.setattr(task_service, "cancel_task", fake_cancel)
    monkeypatch.setattr(task_service, "record", lambda *_args, **_kwargs: None)

    result = asyncio.run(task_service.emergency_stop_all_tasks())

    assert set(cancelled) == {"created", "running"}
    assert set(result["cancelled_task_ids"]) == {"created", "running"}
    assert result["requested"] == 2
    assert result["failed_tasks"] == []
    assert result["ok"] is True


def test_emergency_stop_continues_after_one_task_fails_and_redacts_error(monkeypatch) -> None:
    tasks = [_task("one", TaskPhase.EXECUTION), _task("two", TaskPhase.PLANNING)]
    attempted: list[str] = []

    async def fake_cancel(task_id: str, *, strict=None):  # noqa: ANN001
        attempted.append(task_id)
        if task_id == "one":
            raise RuntimeError(r"failed at C:\Users\Alice\private.txt token=secret-emergency-token")
        return _task(task_id, TaskPhase.CANCELLED)

    monkeypatch.setattr(task_service, "list_tasks", lambda: tasks)
    monkeypatch.setattr(task_service, "cancel_task", fake_cancel)
    monkeypatch.setattr(task_service, "record", lambda *_args, **_kwargs: None)

    result = asyncio.run(task_service.emergency_stop_all_tasks())

    assert set(attempted) == {"one", "two"}
    assert result["ok"] is False
    assert result["cancelled_task_ids"] == ["two"]
    assert result["failed_tasks"][0]["task_id"] == "one"
    error = result["failed_tasks"][0]["error"]
    assert "Alice" not in error
    assert "secret-emergency-token" not in error


def test_runtime_emergency_stop_route_returns_control_plane_summary(monkeypatch) -> None:
    async def fake_emergency_stop():
        return {"ok": True, "requested": 1, "cancelled_task_ids": ["task-1"], "failed_tasks": []}

    monkeypatch.setattr(task_service, "emergency_stop_all_tasks", fake_emergency_stop)
    app = FastAPI()
    app.include_router(routes_runtime.router, prefix="/api")

    response = TestClient(app).post("/api/runtime/emergency-stop")

    assert response.status_code == 200
    assert response.json()["cancelled_task_ids"] == ["task-1"]
