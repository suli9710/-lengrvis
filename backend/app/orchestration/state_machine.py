from __future__ import annotations

from app.core import db
from app.core.audit import record
from app.core.errors import StateTransitionError
from app.core.schemas import LEGACY_TASK_STATUS_MAP, Task, now_iso
from app.orchestration.execution_stage import ExecutionStage, is_stage_transition_allowed
from app.orchestration.task_phase import TaskPhase, is_phase_transition_allowed


PHASE_MAP: dict[str | TaskPhase, tuple[TaskPhase, ExecutionStage]] = {
    **LEGACY_TASK_STATUS_MAP,
    TaskPhase.CREATED: (TaskPhase.CREATED, ExecutionStage.IDLE),
    TaskPhase.GOAL_ANALYSIS: (TaskPhase.GOAL_ANALYSIS, ExecutionStage.IDLE),
    TaskPhase.PLANNING: (TaskPhase.PLANNING, ExecutionStage.IDLE),
    TaskPhase.CONSULTATION: (TaskPhase.CONSULTATION, ExecutionStage.IDLE),
    TaskPhase.PLAN_REVIEW: (TaskPhase.PLAN_REVIEW, ExecutionStage.IDLE),
    TaskPhase.EXECUTION: (TaskPhase.EXECUTION, ExecutionStage.STEP_RUNNING),
    TaskPhase.FINAL_REVIEW: (TaskPhase.FINAL_REVIEW, ExecutionStage.IDLE),
    TaskPhase.COMPLETED: (TaskPhase.COMPLETED, ExecutionStage.IDLE),
    TaskPhase.FAILED: (TaskPhase.FAILED, ExecutionStage.IDLE),
    TaskPhase.CANCELLED: (TaskPhase.CANCELLED, ExecutionStage.IDLE),
}

_STAGE_BY_LEGACY_STATUS: dict[str, ExecutionStage] = {
    key: stage for key, (_phase, stage) in LEGACY_TASK_STATUS_MAP.items()
}


def _phase_of(value: str | TaskPhase) -> TaskPhase:
    if value is None:
        return TaskPhase.CREATED
    if isinstance(value, TaskPhase):
        return value
    text = str(value.value if hasattr(value, "value") else value)
    if text in LEGACY_TASK_STATUS_MAP:
        return LEGACY_TASK_STATUS_MAP[text][0]
    try:
        return TaskPhase(text)
    except ValueError as exc:
        raise StateTransitionError(text, text) from exc


def _stage_for_target(target: str | TaskPhase, phase: TaskPhase) -> ExecutionStage:
    text = str(target.value if hasattr(target, "value") else target)
    if text in _STAGE_BY_LEGACY_STATUS:
        return _STAGE_BY_LEGACY_STATUS[text]
    if phase == TaskPhase.EXECUTION:
        return ExecutionStage.STEP_RUNNING
    return ExecutionStage.IDLE


def _invalid_transition_error(source: TaskPhase, target: TaskPhase) -> StateTransitionError:
    return StateTransitionError(source.value, target.value)


def is_transition_allowed(source: str | TaskPhase, target: str | TaskPhase, *, strict: bool = False) -> bool:
    source_phase = _phase_of(source)
    target_phase = _phase_of(target)
    if source_phase == target_phase:
        return True
    return is_phase_transition_allowed(source_phase, target_phase)


def transition(task: Task, target: str | TaskPhase, actor: str = "StateMachine", *, strict: bool = True) -> Task:
    source_phase = _phase_of(task.status)
    target_phase = _phase_of(target)
    target_stage = _stage_for_target(target, target_phase)
    if source_phase == target_phase and task.phase != source_phase:
        task.phase = source_phase

    if source_phase != target_phase and not is_phase_transition_allowed(source_phase, target_phase):
        if strict:
            raise _invalid_transition_error(source_phase, target_phase)
        _record_invalid_transition(task, actor, source_phase.value, target_phase.value)
    if source_phase == target_phase and task.execution_stage != target_stage:
        if not _is_stage_update_allowed(task.execution_stage, target_stage):
            if strict:
                raise StateTransitionError(task.execution_stage.value, target_stage.value)
            _record_invalid_transition(task, actor, task.execution_stage.value, target_stage.value)

    old_phase = task.status
    old_stage = task.execution_stage
    task.status = target_phase
    task.phase = target_phase
    task.execution_stage = target_stage
    task.updated_at = now_iso()
    db.upsert_model("tasks", task)
    record(
        "task.status_changed",
        actor,
        {
            "from": old_phase,
            "to": target_phase,
            "execution_stage_from": old_stage,
            "execution_stage_to": target_stage,
        },
        task_id=task.id,
    )
    return task


def ensure_transition_allowed(task: Task, target: str | TaskPhase, *, strict: bool = True) -> None:
    source_phase = _phase_of(task.status)
    target_phase = _phase_of(target)
    target_stage = _stage_for_target(target, target_phase)
    if source_phase != target_phase and not is_phase_transition_allowed(source_phase, target_phase):
        if strict:
            raise _invalid_transition_error(source_phase, target_phase)
        return
    if source_phase == target_phase and task.execution_stage != target_stage:
        if not _is_stage_update_allowed(task.execution_stage, target_stage):
            if strict:
                raise StateTransitionError(task.execution_stage.value, target_stage.value)


def safe_transition(
    task: Task,
    target: str | TaskPhase,
    actor: str = "StateMachine",
    *,
    strict: bool | None = None,
) -> Task:
    """Transition using configured strictness.

    Default mode audits invalid transitions and still syncs explicit phase fields;
    strict mode raises StateTransitionError for fail-fast callers and APIs.
    """

    if strict is None:
        try:
            from app.llm.registry import get_effective_settings

            strict = bool(get_effective_settings().strict_state_machine)
        except Exception:
            strict = False
    return transition(task, target, actor=actor, strict=strict)


def sync_phase(task: Task) -> Task:
    phase = _phase_of(task.status)
    if phase != task.phase:
        task.phase = phase
    if phase in {
        TaskPhase.CREATED,
        TaskPhase.GOAL_ANALYSIS,
        TaskPhase.PLANNING,
        TaskPhase.CONSULTATION,
        TaskPhase.PLAN_REVIEW,
        TaskPhase.FINAL_REVIEW,
        TaskPhase.COMPLETED,
        TaskPhase.FAILED,
        TaskPhase.CANCELLED,
    }:
        task.execution_stage = ExecutionStage.IDLE
    else:
        status_text = str(task.status.value if hasattr(task.status, "value") else task.status)
        legacy_stage = _STAGE_BY_LEGACY_STATUS.get(status_text)
        if legacy_stage is not None:
            task.execution_stage = legacy_stage
        elif task.execution_stage == ExecutionStage.IDLE:
            task.execution_stage = ExecutionStage.STEP_RUNNING
        elif not isinstance(task.execution_stage, ExecutionStage):
            task.execution_stage = ExecutionStage.STEP_RUNNING
    return task


def _is_stage_update_allowed(source: ExecutionStage, target: ExecutionStage) -> bool:
    if source == target:
        return True
    if source == ExecutionStage.IDLE and target == ExecutionStage.PAUSED:
        return True
    return is_stage_transition_allowed(source, target)


def _record_invalid_transition(
    task: Task,
    actor: str,
    source: str,
    target: str,
) -> None:
    record(
        "task.invalid_transition_audited",
        actor,
        {"from": source, "to": target, "mode": "non_strict"},
        task_id=task.id,
    )
