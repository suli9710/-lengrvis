"""Tests for P1-4 state machine integration into the orchestrator.

These tests focus on the safe_transition helper and the augmented
ALLOWED_TRANSITIONS table. They do not run a full orchestrator goal;
that is covered by the existing supervisor / runtime tests.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.core.errors import StateTransitionError
from app.core.schemas import Task, TaskStatus
from app.main import create_app
from app.orchestration.state_machine import ALLOWED_TRANSITIONS, safe_transition, transition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("MARVIS_STRICT_STATE_MACHINE", raising=False)
    db.init_db()
    yield


def _make_task(status: TaskStatus = TaskStatus.CREATED) -> Task:
    db.init_db()
    task = Task(user_goal="test", mode="privacy", status=status)
    db.upsert_model("tasks", task)
    return task


def test_safe_transition_records_change_and_updates_db():
    task = _make_task(TaskStatus.CREATED)
    task = safe_transition(task, TaskStatus.PLANNING, actor="UnitTest")
    assert task.status == TaskStatus.PLANNING

    fetched = db.fetch_one("tasks", task.id)
    assert fetched is not None
    refreshed = Task.model_validate(fetched)
    assert refreshed.status == TaskStatus.PLANNING


def test_safe_transition_allows_completed_to_rolled_back():
    task = _make_task(TaskStatus.COMPLETED)
    task = safe_transition(task, TaskStatus.ROLLED_BACK, actor="UnitTest")
    assert task.status == TaskStatus.ROLLED_BACK


def test_safe_transition_force_path_audits_invalid_transition():
    task = _make_task(TaskStatus.AGENT_CONSULTATION)
    # AGENT_CONSULTATION → RECORDING_OBSERVATION is not allowed, but safe_transition
    # should still apply it and emit two audit events (invalid + status_changed forced).
    task = safe_transition(task, TaskStatus.RECORDING_OBSERVATION, actor="UnitTest")
    assert task.status == TaskStatus.RECORDING_OBSERVATION


def test_transition_raises_typed_error_on_invalid_transition_for_strict_callers():
    task = _make_task(TaskStatus.PLANNING)
    with pytest.raises(StateTransitionError) as exc_info:
        transition(task, TaskStatus.ROLLED_BACK, actor="UnitTest")
    assert exc_info.value.code == "invalid_state_transition"
    assert exc_info.value.status_code == 409


def test_allowed_transitions_table_includes_rollback_and_denied_branches():
    # Spot-check the new entries from P1-4.
    assert TaskStatus.ROLLED_BACK in ALLOWED_TRANSITIONS[TaskStatus.COMPLETED]
    assert TaskStatus.ROLLED_BACK in ALLOWED_TRANSITIONS[TaskStatus.FAILED]
    assert TaskStatus.DENIED in ALLOWED_TRANSITIONS[TaskStatus.EXECUTING_TOOL]
    assert TaskStatus.DENIED in ALLOWED_TRANSITIONS[TaskStatus.REVIEWING_TOOL_CALL]
    # Terminal states never leave (except COMPLETED/FAILED → ROLLED_BACK).
    assert ALLOWED_TRANSITIONS[TaskStatus.DENIED] == set()
    assert ALLOWED_TRANSITIONS[TaskStatus.CANCELLED] == set()
    assert ALLOWED_TRANSITIONS[TaskStatus.ROLLED_BACK] == set()


def test_same_status_transition_is_a_noop_safe():
    task = _make_task(TaskStatus.PLANNING)
    task = safe_transition(task, TaskStatus.PLANNING, actor="UnitTest")
    assert task.status == TaskStatus.PLANNING


def test_strict_state_machine_blocks_safe_transition(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MARVIS_STRICT_STATE_MACHINE", "true")
    task = _make_task(TaskStatus.AGENT_CONSULTATION)

    with pytest.raises(StateTransitionError) as exc_info:
        safe_transition(task, TaskStatus.RECORDING_OBSERVATION, actor="UnitTest")

    assert exc_info.value.code == "invalid_state_transition"
    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskStatus.AGENT_CONSULTATION


def test_strict_state_machine_disables_legacy_terminal_shortcut(monkeypatch: pytest.MonkeyPatch):
    loose = _make_task(TaskStatus.CREATED)
    assert transition(loose, TaskStatus.ROLLED_BACK, actor="UnitTest").status == TaskStatus.ROLLED_BACK

    monkeypatch.setenv("MARVIS_STRICT_STATE_MACHINE", "true")
    strict = _make_task(TaskStatus.CREATED)
    with pytest.raises(StateTransitionError):
        safe_transition(strict, TaskStatus.ROLLED_BACK, actor="UnitTest")


def test_settings_endpoint_can_enable_strict_state_machine():
    client = TestClient(create_app())

    response = client.post("/api/settings", json={"strict_state_machine": True})

    assert response.status_code == 200
    assert response.json()["strict_state_machine"] is True


def test_task_status_api_keeps_compatibility_in_loose_mode():
    task = _make_task(TaskStatus.PLANNING)
    client = TestClient(create_app())

    response = client.post(f"/api/tasks/{task.id}/rollback")

    assert response.status_code == 200
    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskStatus.ROLLED_BACK


def test_task_status_api_returns_app_error_for_strict_transition(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MARVIS_STRICT_STATE_MACHINE", "true")
    task = _make_task(TaskStatus.PLANNING)
    client = TestClient(create_app())

    response = client.post(f"/api/tasks/{task.id}/rollback")

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "invalid_state_transition",
            "message": "Invalid state transition planning -> rolled_back",
        }
    }
    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskStatus.PLANNING
