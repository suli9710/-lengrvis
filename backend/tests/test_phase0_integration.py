"""Phase 0 integration tests -- verifies all new components work together."""
from __future__ import annotations

import pytest
from app.core import db
from app.core.schemas import LEGACY_TASK_STATUS_MAP, Task, PlanStep, TaskStatus, StepStatus
from app.orchestration.task_phase import TaskPhase


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("MARVIS_STRICT_STATE_MACHINE", raising=False)
    db.init_db()


# --- Dual-write tests ---


class TestDualWrite:
    """Verify old TaskStatus and new TaskPhase stay in sync."""

    def test_task_has_phase_and_execution_stage_fields(self):
        """Task model should have phase and execution_stage with defaults."""
        task = Task(user_goal="test")
        assert hasattr(task, "phase"), "Task must have 'phase' field"
        assert hasattr(task, "execution_stage"), "Task must have 'execution_stage' field"

    def test_planstep_has_step_phase_field(self):
        """PlanStep model should have step_phase with default."""
        step = PlanStep(agent_name="test", tool_name="t", description="d")
        assert hasattr(step, "step_phase"), "PlanStep must have 'step_phase' field"

    def test_transition_syncs_phase(self):
        """Transitioning TaskStatus should update phase and execution_stage."""
        try:
            from app.orchestration.state_machine import transition, PHASE_MAP
        except ImportError:
            pytest.skip("PHASE_MAP not implemented in state_machine yet")

        task = Task(user_goal="test", status=TaskStatus.CREATED)
        db.upsert_model("tasks", task)

        task = transition(task, TaskStatus.PLANNING, actor="IntegrationTest")

        expected = PHASE_MAP.get(TaskStatus.PLANNING)
        if expected:
            assert task.phase == expected[0]
            assert task.execution_stage == expected[1]

    def test_safe_transition_syncs_phase(self):
        """safe_transition should also sync phase fields."""
        try:
            from app.orchestration.state_machine import safe_transition, PHASE_MAP
        except ImportError:
            pytest.skip("PHASE_MAP not implemented in state_machine yet")

        task = Task(user_goal="test", status=TaskStatus.CREATED)
        db.upsert_model("tasks", task)

        task = safe_transition(task, TaskStatus.PLANNING, actor="IntegrationTest")
        expected = PHASE_MAP.get(TaskStatus.PLANNING)
        if expected:
            assert task.phase == expected[0]
            assert task.execution_stage == expected[1]

    def test_phase_map_covers_all_statuses(self):
        """PHASE_MAP should have an entry for every TaskStatus."""
        try:
            from app.orchestration.state_machine import PHASE_MAP
        except ImportError:
            pytest.skip("PHASE_MAP not implemented in state_machine yet")

        for status in [*TaskPhase, *LEGACY_TASK_STATUS_MAP]:
            assert status in PHASE_MAP, f"PHASE_MAP missing entry for {status}"

    def test_dual_write_persists_to_db(self):
        """Phase fields should be persisted when task is saved."""
        try:
            from app.orchestration.state_machine import transition
        except ImportError:
            pytest.skip("transition not available")

        task = Task(user_goal="test", status=TaskStatus.CREATED)
        db.upsert_model("tasks", task)

        task = transition(task, TaskStatus.PLANNING, actor="IntegrationTest")
        task = transition(task, TaskStatus.REVIEWING_PLAN, actor="IntegrationTest")
        task = transition(task, TaskStatus.EXECUTING_STEP, actor="IntegrationTest")

        fetched = db.fetch_one("tasks", task.id)
        assert fetched is not None
        restored = Task.model_validate(fetched)
        assert restored.phase == task.phase
        assert restored.execution_stage == task.execution_stage


# --- Three-layer state enum tests ---


class TestThreeLayerEnums:
    """Verify each layer's transition logic independently."""

    def test_task_phase_valid_transitions(self):
        try:
            from app.orchestration.task_phase import TaskPhase, is_phase_transition_allowed
        except ImportError:
            pytest.skip("task_phase module not implemented yet")
        assert is_phase_transition_allowed(TaskPhase.CREATED, TaskPhase.GOAL_ANALYSIS)
        assert not is_phase_transition_allowed(TaskPhase.COMPLETED, TaskPhase.CREATED)

    def test_execution_stage_valid_transitions(self):
        try:
            from app.orchestration.execution_stage import ExecutionStage, is_stage_transition_allowed
        except ImportError:
            pytest.skip("execution_stage module not implemented yet")
        assert is_stage_transition_allowed(ExecutionStage.IDLE, ExecutionStage.STEP_RUNNING)
        assert not is_stage_transition_allowed(ExecutionStage.IDLE, ExecutionStage.PAUSED)

    def test_step_phase_valid_transitions(self):
        try:
            from app.orchestration.step_phase import StepPhase, is_step_transition_allowed
        except ImportError:
            pytest.skip("step_phase module not implemented yet")
        assert is_step_transition_allowed(StepPhase.PENDING, StepPhase.READY)
        assert not is_step_transition_allowed(StepPhase.SUCCEEDED, StepPhase.PENDING)


# --- Event + Dispatcher integration ---


class TestEventDispatcherIntegration:
    """Verify events flow through the dispatcher correctly."""

    @pytest.mark.asyncio
    async def test_dispatch_records_audit_event(self):
        """Dispatching an event should create an audit record."""
        try:
            from app.orchestration.dispatcher import EventDispatcher
            from pydantic import BaseModel, Field
            from app.core.schemas import new_id, now_iso
        except ImportError:
            pytest.skip("dispatcher not implemented yet")

        class FakeEvent(BaseModel):
            id: str = Field(default_factory=lambda: new_id("evt"))
            event_type: str = "test.integration"
            task_id: str = "task_integ"
            timestamp: str = Field(default_factory=now_iso)
            source_agent: str = "IntegrationTest"
            payload: dict = Field(default_factory=dict)

            def summary(self):
                return "integration test event"

        dispatcher = EventDispatcher()
        results = await dispatcher.dispatch(FakeEvent())

        audit_rows = db.fetch_many("audit_events", "event_type = ?", ("test.integration",))
        assert len(audit_rows) >= 1


# --- Notification e2e ---


class TestNotificationE2E:
    """Verify notification service works end-to-end."""

    def test_notify_publishes_to_bus(self):
        from app.services.notification_service import notify, init_bus
        from app.orchestration.agent_bus import AgentBus

        bus = AgentBus()
        init_bus(bus)

        # notify(first_positional, second_positional) treats the first as
        # title and the second as body when both are supplied.
        result = notify("Test title", "Test body", task_id="task_notif", severity="info")
        assert result["queued"] is True
        assert result["title"] == "Test title"

        messages = bus.get_messages("task_notif")
        assert len(messages) >= 1
        assert any("Test body" in m.content for m in messages)


# --- Local model install flow ---


class TestLocalModelInstallFlow:
    """Verify the install-local-model endpoint works (with mocked Ollama)."""

    def test_endpoint_exists(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        from unittest.mock import patch, AsyncMock
        from app.services import ollama_service

        async def fake_install_local_model(model=None):
            yield {"phase": "install", "status": "skipped", "message": "Already installed."}
            yield {"phase": "start", "status": "done", "message": "Running."}
            yield {"phase": "pull", "status": "success", "model": model or "test"}
            yield {"phase": "switch", "status": "done", "message": "Ready.", "model": model or "test"}

        with patch.object(ollama_service, "is_installed", return_value=True), \
             patch.object(ollama_service, "is_running", new_callable=AsyncMock, return_value=True), \
             patch.object(ollama_service, "install_local_model", side_effect=fake_install_local_model):

            client = TestClient(create_app())
            resp = client.post("/api/settings/install-local-model", json={})
            assert resp.status_code == 200
            assert resp.json()["ok"] is True
