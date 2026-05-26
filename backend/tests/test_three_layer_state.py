"""Tests for three-layer state decomposition (TaskPhase, ExecutionStage, StepPhase).

Covers:
- Valid transitions for each layer
- Invalid transitions raise StateTransitionError
- PHASE_MAP covers all TaskStatus values
- sync_phase correctly maps each TaskStatus
- Dual-write integration with transition() and safe_transition()
"""

from __future__ import annotations

import pytest

from app.core import db
from app.core.errors import StateTransitionError
from app.core.schemas import LEGACY_TASK_STATUS_MAP, PlanStep, StepStatus, Task, TaskStatus
from app.orchestration.execution_stage import (
    EXECUTION_STAGE_TRANSITIONS,
    ExecutionStage,
    is_stage_transition_allowed,
    stage_transition,
)
from app.orchestration.state_machine import (
    PHASE_MAP,
    safe_transition,
    sync_phase,
    transition,
)
from app.orchestration.step_phase import (
    STEP_PHASE_TRANSITIONS,
    StepPhase,
    is_step_transition_allowed,
    set_step_status,
    step_phase_for_status,
    step_phase_transition,
)
from app.orchestration.task_phase import (
    TASK_PHASE_TRANSITIONS,
    TaskPhase,
    is_phase_transition_allowed,
    phase_transition,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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


# ===========================================================================
# Layer 1: TaskPhase
# ===========================================================================

class TestTaskPhaseTransitions:
    """Verify every valid transition in the TaskPhase layer."""

    @pytest.mark.parametrize(
        "source,target",
        [
            (TaskPhase.CREATED, TaskPhase.GOAL_ANALYSIS),
            (TaskPhase.CREATED, TaskPhase.CANCELLED),
            (TaskPhase.GOAL_ANALYSIS, TaskPhase.PLANNING),
            (TaskPhase.GOAL_ANALYSIS, TaskPhase.FAILED),
            (TaskPhase.GOAL_ANALYSIS, TaskPhase.CANCELLED),
            (TaskPhase.PLANNING, TaskPhase.CONSULTATION),
            (TaskPhase.PLANNING, TaskPhase.PLAN_REVIEW),
            (TaskPhase.PLANNING, TaskPhase.FAILED),
            (TaskPhase.PLANNING, TaskPhase.CANCELLED),
            (TaskPhase.CONSULTATION, TaskPhase.PLAN_REVIEW),
            (TaskPhase.CONSULTATION, TaskPhase.PLANNING),
            (TaskPhase.CONSULTATION, TaskPhase.FAILED),
            (TaskPhase.CONSULTATION, TaskPhase.CANCELLED),
            (TaskPhase.PLAN_REVIEW, TaskPhase.EXECUTION),
            (TaskPhase.PLAN_REVIEW, TaskPhase.PLANNING),
            (TaskPhase.PLAN_REVIEW, TaskPhase.CONSULTATION),
            (TaskPhase.PLAN_REVIEW, TaskPhase.FAILED),
            (TaskPhase.PLAN_REVIEW, TaskPhase.CANCELLED),
            (TaskPhase.EXECUTION, TaskPhase.FINAL_REVIEW),
            (TaskPhase.EXECUTION, TaskPhase.COMPLETED),
            (TaskPhase.EXECUTION, TaskPhase.FAILED),
            (TaskPhase.EXECUTION, TaskPhase.CANCELLED),
            (TaskPhase.FINAL_REVIEW, TaskPhase.COMPLETED),
            (TaskPhase.FINAL_REVIEW, TaskPhase.EXECUTION),
            (TaskPhase.FINAL_REVIEW, TaskPhase.FAILED),
            (TaskPhase.FINAL_REVIEW, TaskPhase.CANCELLED),
        ],
    )
    def test_valid_phase_transition(self, source: TaskPhase, target: TaskPhase):
        assert is_phase_transition_allowed(source, target)
        result = phase_transition(source, target)
        assert result == target

    @pytest.mark.parametrize(
        "source,target",
        [
            (TaskPhase.CREATED, TaskPhase.EXECUTION),
            (TaskPhase.COMPLETED, TaskPhase.CREATED),
            (TaskPhase.FAILED, TaskPhase.PLANNING),
            (TaskPhase.CANCELLED, TaskPhase.GOAL_ANALYSIS),
            (TaskPhase.EXECUTION, TaskPhase.CREATED),
            (TaskPhase.GOAL_ANALYSIS, TaskPhase.EXECUTION),
        ],
    )
    def test_invalid_phase_transition_raises(self, source: TaskPhase, target: TaskPhase):
        assert not is_phase_transition_allowed(source, target)
        with pytest.raises(StateTransitionError):
            phase_transition(source, target)

    def test_terminal_phases_have_no_transitions(self):
        for terminal in (TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED):
            assert TASK_PHASE_TRANSITIONS[terminal] == set()


# ===========================================================================
# Layer 2: ExecutionStage
# ===========================================================================

class TestExecutionStageTransitions:
    """Verify every valid transition in the ExecutionStage layer."""

    @pytest.mark.parametrize(
        "source,target",
        [
            (ExecutionStage.IDLE, ExecutionStage.STEP_RUNNING),
            (ExecutionStage.STEP_RUNNING, ExecutionStage.AWAITING_APPROVAL),
            (ExecutionStage.STEP_RUNNING, ExecutionStage.IDLE),
            (ExecutionStage.STEP_RUNNING, ExecutionStage.PAUSED),
            (ExecutionStage.AWAITING_APPROVAL, ExecutionStage.STEP_RUNNING),
            (ExecutionStage.AWAITING_APPROVAL, ExecutionStage.IDLE),
            (ExecutionStage.AWAITING_APPROVAL, ExecutionStage.PAUSED),
            (ExecutionStage.PAUSED, ExecutionStage.STEP_RUNNING),
            (ExecutionStage.PAUSED, ExecutionStage.IDLE),
        ],
    )
    def test_valid_stage_transition(self, source: ExecutionStage, target: ExecutionStage):
        assert is_stage_transition_allowed(source, target)
        result = stage_transition(source, target)
        assert result == target

    @pytest.mark.parametrize(
        "source,target",
        [
            (ExecutionStage.IDLE, ExecutionStage.AWAITING_APPROVAL),
            (ExecutionStage.IDLE, ExecutionStage.PAUSED),
            (ExecutionStage.PAUSED, ExecutionStage.AWAITING_APPROVAL),
        ],
    )
    def test_invalid_stage_transition_raises(self, source: ExecutionStage, target: ExecutionStage):
        assert not is_stage_transition_allowed(source, target)
        with pytest.raises(StateTransitionError):
            stage_transition(source, target)


# ===========================================================================
# Layer 3: StepPhase
# ===========================================================================

class TestStepPhaseTransitions:
    """Verify every valid transition in the StepPhase layer."""

    @pytest.mark.parametrize(
        "source,target",
        [
            (StepPhase.PENDING, StepPhase.READY),
            (StepPhase.READY, StepPhase.RUNNING),
            (StepPhase.READY, StepPhase.FAILED),
            (StepPhase.RUNNING, StepPhase.TOOL_REVIEW),
            (StepPhase.RUNNING, StepPhase.TOOL_EXECUTING),
            (StepPhase.RUNNING, StepPhase.SUCCEEDED),
            (StepPhase.RUNNING, StepPhase.FAILED),
            (StepPhase.TOOL_REVIEW, StepPhase.TOOL_EXECUTING),
            (StepPhase.TOOL_REVIEW, StepPhase.RUNNING),
            (StepPhase.TOOL_REVIEW, StepPhase.FAILED),
            (StepPhase.TOOL_EXECUTING, StepPhase.OBSERVING),
            (StepPhase.TOOL_EXECUTING, StepPhase.RUNNING),
            (StepPhase.TOOL_EXECUTING, StepPhase.FAILED),
            (StepPhase.OBSERVING, StepPhase.RUNNING),
            (StepPhase.OBSERVING, StepPhase.SUCCEEDED),
            (StepPhase.OBSERVING, StepPhase.FAILED),
        ],
    )
    def test_valid_step_transition(self, source: StepPhase, target: StepPhase):
        assert is_step_transition_allowed(source, target)
        result = step_phase_transition(source, target)
        assert result == target

    @pytest.mark.parametrize(
        "source,target",
        [
            (StepPhase.PENDING, StepPhase.RUNNING),
            (StepPhase.SUCCEEDED, StepPhase.RUNNING),
            (StepPhase.FAILED, StepPhase.READY),
            (StepPhase.OBSERVING, StepPhase.TOOL_REVIEW),
        ],
    )
    def test_invalid_step_transition_raises(self, source: StepPhase, target: StepPhase):
        assert not is_step_transition_allowed(source, target)
        with pytest.raises(StateTransitionError):
            step_phase_transition(source, target)

    def test_terminal_step_phases_have_no_transitions(self):
        for terminal in (StepPhase.SUCCEEDED, StepPhase.FAILED):
            assert STEP_PHASE_TRANSITIONS[terminal] == set()

    @pytest.mark.parametrize(
        "status,phase",
        [
            (StepStatus.PENDING, StepPhase.PENDING),
            (StepStatus.PROPOSED, StepPhase.READY),
            (StepStatus.REVIEWED, StepPhase.RUNNING),
            (StepStatus.APPROVED, StepPhase.TOOL_EXECUTING),
            (StepStatus.RUNNING, StepPhase.TOOL_EXECUTING),
            (StepStatus.WAITING_USER_APPROVAL, StepPhase.TOOL_REVIEW),
            (StepStatus.SUCCEEDED, StepPhase.SUCCEEDED),
            (StepStatus.SKIPPED, StepPhase.SUCCEEDED),
            (StepStatus.FAILED, StepPhase.FAILED),
            (StepStatus.DENIED, StepPhase.FAILED),
        ],
    )
    def test_step_status_maps_to_runtime_phase(self, status: StepStatus, phase: StepPhase):
        assert step_phase_for_status(status) == phase

    def test_set_step_status_syncs_phase_in_non_strict_mode(self):
        step = PlanStep(agent_name="TestAgent", tool_name="test_tool", description="test")

        set_step_status(step, StepStatus.FAILED, actor="UnitTest")

        assert step.status == StepStatus.FAILED
        assert step.step_phase == StepPhase.FAILED


# ===========================================================================
# PHASE_MAP coverage
# ===========================================================================

class TestPhaseMapCoverage:
    """Ensure PHASE_MAP covers new phases and legacy persisted status strings."""

    def test_phase_map_covers_all_task_phases(self):
        for phase in TaskPhase:
            assert phase in PHASE_MAP, f"TaskPhase.{phase.name} missing from PHASE_MAP"

    def test_phase_map_covers_legacy_status_strings(self):
        for status in LEGACY_TASK_STATUS_MAP:
            assert status in PHASE_MAP, f"legacy status {status} missing from PHASE_MAP"

    def test_phase_map_values_are_correct_types(self):
        for status, (phase, stage) in PHASE_MAP.items():
            assert isinstance(phase, TaskPhase), f"Bad phase type for {status}"
            assert isinstance(stage, ExecutionStage), f"Bad stage type for {status}"


# ===========================================================================
# sync_phase
# ===========================================================================

class TestSyncPhase:
    """Verify sync_phase correctly maps current and legacy status values."""

    @pytest.mark.parametrize("phase", list(TaskPhase))
    def test_sync_phase_maps_every_phase(self, phase: TaskPhase):
        task = Task(user_goal="test", mode="privacy", status=phase)
        sync_phase(task)
        expected_phase, expected_stage = PHASE_MAP[phase]
        assert task.phase == expected_phase, (
            f"For {phase}: expected phase={expected_phase}, got {task.phase}"
        )
        assert task.execution_stage == expected_stage, (
            f"For {phase}: expected stage={expected_stage}, got {task.execution_stage}"
        )

    def test_sync_phase_specific_mappings(self):
        """Spot-check key mappings."""
        task = Task(user_goal="test", mode="privacy", status=TaskStatus.EXECUTING_STEP)
        sync_phase(task)
        assert task.phase == TaskPhase.EXECUTION
        assert task.execution_stage == ExecutionStage.STEP_RUNNING

        task.status = TaskStatus.WAITING_USER_APPROVAL
        sync_phase(task)
        assert task.phase == TaskPhase.EXECUTION
        assert task.execution_stage == ExecutionStage.AWAITING_APPROVAL

        task.status = TaskStatus.PAUSED
        sync_phase(task)
        assert task.phase == TaskPhase.EXECUTION
        assert task.execution_stage == ExecutionStage.PAUSED

        task.status = TaskStatus.COMPLETED
        sync_phase(task)
        assert task.phase == TaskPhase.COMPLETED
        assert task.execution_stage == ExecutionStage.IDLE


# ===========================================================================
# Dual-write integration
# ===========================================================================

class TestDualWriteIntegration:
    """Verify that transition() and safe_transition() keep phase fields in sync."""

    def test_transition_sets_phase_and_stage(self):
        task = _make_task(TaskStatus.CREATED)
        task = transition(task, TaskStatus.PLANNING, actor="Test")
        assert task.status == TaskStatus.PLANNING
        assert task.phase == TaskPhase.PLANNING
        assert task.execution_stage == ExecutionStage.IDLE

    def test_transition_to_executing_step_sets_step_running(self):
        task = _make_task(TaskStatus.REVIEWING_PLAN)
        task = transition(task, TaskStatus.EXECUTING_STEP, actor="Test")
        assert task.phase == TaskPhase.EXECUTION
        assert task.execution_stage == ExecutionStage.STEP_RUNNING

    def test_safe_transition_same_status_syncs_phase(self):
        task = _make_task(TaskStatus.EXECUTING_STEP)
        # Force bad phase to verify sync fixes it
        task.phase = TaskPhase.CREATED
        task.execution_stage = ExecutionStage.IDLE
        db.upsert_model("tasks", task)

        task = safe_transition(task, TaskStatus.EXECUTING_STEP, actor="Test")
        assert task.phase == TaskPhase.EXECUTION
        assert task.execution_stage == ExecutionStage.STEP_RUNNING

    def test_safe_transition_force_path_syncs_phase(self):
        task = _make_task(TaskStatus.REVIEWING_PLAN)
        # Legacy fine-grained execution aliases now stay within TaskPhase.EXECUTION
        # and update only the execution stage.
        task = safe_transition(task, TaskStatus.RECORDING_OBSERVATION, actor="Test")
        assert task.status == TaskPhase.EXECUTION
        assert task.phase == TaskPhase.EXECUTION
        assert task.execution_stage == ExecutionStage.STEP_RUNNING

    def test_dual_write_persisted_to_db(self):
        task = _make_task(TaskStatus.CREATED)
        task = transition(task, TaskStatus.PLANNING, actor="Test")

        fetched = db.fetch_one("tasks", task.id)
        assert fetched is not None
        refreshed = Task.model_validate(fetched)
        assert refreshed.phase == TaskPhase.PLANNING
        assert refreshed.execution_stage == ExecutionStage.IDLE

    def test_full_lifecycle_dual_write(self):
        """Walk a task through several statuses and verify phase stays in sync."""
        task = _make_task(TaskStatus.CREATED)

        task = transition(task, TaskStatus.PLANNING, actor="Test")
        assert task.phase == TaskPhase.PLANNING
        assert task.execution_stage == ExecutionStage.IDLE

        task = transition(task, TaskStatus.REVIEWING_PLAN, actor="Test")
        assert task.phase == TaskPhase.PLAN_REVIEW
        assert task.execution_stage == ExecutionStage.IDLE

        task = transition(task, TaskStatus.EXECUTING_STEP, actor="Test")
        assert task.phase == TaskPhase.EXECUTION
        assert task.execution_stage == ExecutionStage.STEP_RUNNING

        task = transition(task, TaskStatus.FINAL_REVIEW, actor="Test")
        assert task.phase == TaskPhase.FINAL_REVIEW
        assert task.execution_stage == ExecutionStage.IDLE

        task = transition(task, TaskStatus.COMPLETED, actor="Test")
        assert task.phase == TaskPhase.COMPLETED
        assert task.execution_stage == ExecutionStage.IDLE


# ===========================================================================
# Model defaults
# ===========================================================================

class TestModelDefaults:
    """Verify new fields have correct defaults."""

    def test_task_default_fields(self):
        task = Task(user_goal="test")
        assert task.phase == TaskPhase.CREATED
        assert task.execution_stage == ExecutionStage.IDLE

    def test_plan_step_default_fields(self):
        step = PlanStep(agent_name="TestAgent", tool_name="test_tool", description="test")
        assert step.step_phase == StepPhase.PENDING
