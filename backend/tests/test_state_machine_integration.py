"""Integration tests for the three-layer task state machine."""

from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from native_confirmation_helpers import (
    TEST_NATIVE_CONFIRMATION_SECRET,
    native_confirmation_headers,
    signed_native_confirmation_headers,
)

from app.core import db
from app.core.errors import StateTransitionError
from app.core.schemas import Task, TaskStatus
from app.main import create_app
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.state_machine import safe_transition, transition
from app.orchestration.task_phase import TaskPhase
from app.security.native_confirmation import NATIVE_CONFIRMATION_PUBLIC_KEY_ENV


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_SECRET", TEST_NATIVE_CONFIRMATION_SECRET)
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


def test_set_status_writes_terminal_summary_atomically(monkeypatch: pytest.MonkeyPatch):
    from app.agents.orchestrator_agent import OrchestratorAgent

    task = _make_task(TaskStatus.PLANNING)
    writes: list[Task] = []
    original_upsert = db.upsert_model

    def spy_upsert(table: str, model, **kwargs):
        if table == "tasks" and getattr(model, "id", "") == task.id:
            writes.append(Task.model_validate(model.model_dump(mode="python")))
        return original_upsert(table, model, **kwargs)

    monkeypatch.setattr(db, "upsert_model", spy_upsert)

    OrchestratorAgent()._set_status(task, TaskStatus.DENIED, final_summary="Denied: policy blocked this task.")

    assert writes
    assert writes[0].status == TaskPhase.DENIED
    assert writes[0].final_summary == "Denied: policy blocked this task."


def test_denial_and_user_cancellation_persist_as_distinct_terminal_phases():
    denied = _make_task(TaskStatus.PLANNING)
    cancelled = _make_task(TaskStatus.PLANNING)

    safe_transition(denied, TaskStatus.DENIED, actor="SafetyReviewAgent", strict=True)
    safe_transition(cancelled, TaskStatus.CANCELLED, actor="TaskService", strict=True)

    denied_row = db.fetch_one("tasks", denied.id)
    cancelled_row = db.fetch_one("tasks", cancelled.id)
    assert denied_row["status"] == "denied"
    assert denied_row["phase"] == "denied"
    assert cancelled_row["status"] == "cancelled"
    assert cancelled_row["phase"] == "cancelled"

    client = TestClient(create_app())
    assert client.get(f"/api/tasks/{denied.id}").json()["status"] == "denied"
    assert client.get(f"/api/tasks/{cancelled.id}").json()["status"] == "cancelled"


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

    assert response.status_code == 403
    assert "Native confirmation proof is required" in response.json()["detail"]

    response = client.post(
        f"/api/tasks/{task.id}/rollback",
        headers=native_confirmation_headers(
            "rollback_task",
            task.id,
            endpoint=f"/api/tasks/{task.id}/rollback",
        ),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Invalid state transition planning -> rolled_back",
        "error": {
            "code": "invalid_state_transition",
            "message": "Invalid state transition planning -> rolled_back",
        },
    }
    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskPhase.PLANNING


def test_rollback_accepts_ed25519_native_confirmation_challenge(monkeypatch: pytest.MonkeyPatch):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, _public_key_b64(private_key))
    task = _make_task(TaskStatus.FAILED)
    endpoint = f"/api/tasks/{task.id}/rollback"
    client = TestClient(create_app())

    challenge = client.post(f"{endpoint}/native-confirmation-challenge")
    assert challenge.status_code == 200, challenge.text
    monkeypatch.setattr(
        "app.api.routes_tasks.rollback_tools.execute_rollback",
        lambda _task_id: {
            "task_id": task.id,
            "executed": [{"tool_call_id": "tool-1", "ok": True}],
            "count": 1,
            "state": "succeeded",
            "attempted": 1,
            "succeeded": 1,
            "verified": 1,
            "verification_failed": 0,
            "failed": 0,
            "manual_required": 0,
            "unrecoverable": 0,
        },
    )
    response = client.post(endpoint, headers=signed_native_confirmation_headers(challenge.json(), private_key))

    assert response.status_code == 200
    assert response.json()["native_confirmation"]["confirmation_id"] == challenge.json()["confirmation_id"]
    assert response.json()["task_status"] == TaskPhase.ROLLED_BACK.value
    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskPhase.ROLLED_BACK
    assert persisted.metadata["rollback"] == {
        "state": "succeeded",
        "attempted": 1,
        "succeeded": 1,
        "verified": 1,
        "verification_failed": 0,
        "failed": 0,
        "manual_required": 0,
        "unrecoverable": 0,
    }
    assert persisted.final_summary == "Rollback completed successfully: 1 of 1 actions restored."


def test_partial_rollback_transitions_task_to_repair_required(monkeypatch: pytest.MonkeyPatch):
    task = _make_task(TaskStatus.COMPLETED)
    endpoint = f"/api/tasks/{task.id}/rollback"
    monkeypatch.setattr(
        "app.api.routes_tasks.rollback_tools.execute_rollback",
        lambda _task_id: {
            "task_id": task.id,
            "executed": [
                {"tool_call_id": "tool-1", "ok": True},
                {"tool_call_id": "tool-2", "ok": False, "requires_user_action": True},
            ],
            "count": 2,
            "state": "manual_required",
            "attempted": 2,
            "succeeded": 1,
            "verified": 1,
            "verification_failed": 0,
            "failed": 0,
            "manual_required": 1,
            "unrecoverable": 0,
        },
    )

    response = TestClient(create_app()).post(
        endpoint,
        headers=native_confirmation_headers("rollback_task", task.id, endpoint=endpoint),
    )

    assert response.status_code == 200
    assert response.json()["task_status"] == TaskPhase.REPAIR_REQUIRED.value
    persisted = Task.model_validate(db.fetch_one("tasks", task.id))
    assert persisted.status == TaskPhase.REPAIR_REQUIRED
    assert persisted.execution_stage == ExecutionStage.IDLE
    assert persisted.metadata["rollback"]["state"] == "manual_required"
    assert "requires manual repair" in persisted.final_summary.lower()


def _public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return (
        base64.urlsafe_b64encode(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        .decode("ascii")
        .rstrip("=")
    )
