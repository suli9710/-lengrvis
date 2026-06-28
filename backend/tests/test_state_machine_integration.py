"""Integration tests for the three-layer task state machine."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.errors import StateTransitionError
from app.core.schemas import Task, TaskStatus
from app.main import create_app
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.state_machine import safe_transition, transition
from app.orchestration.task_phase import TaskPhase


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LENGRVIS_STRICT_STATE_MACHINE", raising=False)
    db.init_db()
    yield


def _make_task(status=TaskStatus.CREATED) -> Task:
    db.init_db()
    task = Task(user_goal="test", mode="privacy", status=status)
    db.upsert_model("tasks", task)
    return task


def test_safe_transition_records_change_and_updates_db():
    task = _make_task(TaskStatus.CREATED)

    task = safe_transition(task, TaskStatus.PLANNING, actor="UnitTest")

    assert task.status == TaskPhase.PLANNING
    fetched = db.fetch_one("tasks", task.id)
    assert fetched is not None
    refreshed = Task.model_validate(fetched)
    assert refreshed.status == TaskPhase.PLANNING
    assert refreshed.phase == TaskPhase.PLANNING


def test_execution_stage_aliases_do_not_change_phase():
    task = _make_task(TaskStatus.REVIEWING_PLAN)

    task = safe_transition(task, TaskStatus.EXECUTING_STEP, actor="UnitTest")
    assert task.status == TaskPhase.EXECUTION
    assert task.execution_stage == ExecutionStage.STEP_RUNNING

    task = safe_transition(task, TaskStatus.WAITING_USER_APPROVAL, actor="UnitTest")
    assert task.status == TaskPhase.EXECUTION
    assert task.execution_stage == ExecutionStage.AWAITING_APPROVAL

    task = safe_transition(task, TaskStatus.EXECUTING_TOOL, actor="UnitTest")
    assert task.status == TaskPhase.EXECUTION
    assert task.execution_stage == ExecutionStage.STEP_RUNNING


def test_transition_raises_typed_error_on_invalid_phase_transition():
    task = _make_task(TaskStatus.CREATED)

    with pytest.raises(StateTransitionError) as exc_info:
        transition(task, TaskStatus.EXECUTING_STEP, actor="UnitTest")

    assert exc_info.value.code == "invalid_state_transition"
    assert exc_info.value.status_code == 409


def test_safe_transition_no_longer_forces_invalid_transition():
    # created -> completed skips every intermediate phase and stays invalid
    # (created -> failed became legal so pre-planning crashes can terminate).
    task = _make_task(TaskStatus.CREATED)

    task = safe_transition(task, TaskStatus.COMPLETED, actor="UnitTest")

    assert task.status == TaskPhase.CREATED


def test_safe_transition_strict_raises_invalid_transition():
    task = _make_task(TaskStatus.CREATED)

    with pytest.raises(StateTransitionError):
        safe_transition(task, TaskStatus.COMPLETED, actor="UnitTest", strict=True)

    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskPhase.CREATED


def test_set_status_writes_summary_only_after_successful_transition(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from app.agents.orchestrator_agent import OrchestratorAgent

    monkeypatch.setenv("LENGRVIS_STRICT_STATE_MACHINE", "false")
    task = _make_task(TaskStatus.CREATED)
    task.final_summary = "initial"
    db.upsert_model("tasks", task)

    OrchestratorAgent()._set_status(task, TaskStatus.COMPLETED, final_summary="must not persist")

    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskPhase.CREATED
    assert persisted.final_summary == "initial"


def test_set_status_persists_summary_after_valid_transition():
    from app.agents.orchestrator_agent import OrchestratorAgent

    task = _make_task(TaskStatus.PLANNING)

    OrchestratorAgent()._set_status(task, TaskStatus.FAILED, final_summary="done")

    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskPhase.FAILED
    assert persisted.final_summary == "done"


def test_same_status_transition_syncs_phase():
    task = _make_task(TaskStatus.PLANNING)
    task.phase = TaskPhase.CREATED
    db.upsert_model("tasks", task)

    task = safe_transition(task, TaskStatus.PLANNING, actor="UnitTest")

    assert task.status == TaskPhase.PLANNING
    assert task.phase == TaskPhase.PLANNING


def test_settings_endpoint_can_enable_strict_state_machine():
    client = TestClient(create_app())

    response = client.post("/api/settings", json={"strict_state_machine": True})

    assert response.status_code == 200
    assert response.json()["strict_state_machine"] is True


def test_task_status_api_returns_app_error_for_invalid_transition():
    task = _make_task(TaskStatus.PLANNING)
    client = TestClient(create_app())

    response = client.post(f"/api/tasks/{task.id}/rollback")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Invalid state transition planning -> failed",
        "error": {
            "code": "invalid_state_transition",
            "message": "Invalid state transition planning -> failed",
        },
    }
    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskPhase.PLANNING
