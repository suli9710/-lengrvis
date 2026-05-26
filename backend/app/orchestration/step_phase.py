from __future__ import annotations

from enum import StrEnum

from app.core.audit import record


class StepPhase(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    TOOL_REVIEW = "tool_review"
    TOOL_EXECUTING = "tool_executing"
    OBSERVING = "observing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


STEP_PHASE_TRANSITIONS: dict[StepPhase, set[StepPhase]] = {
    StepPhase.PENDING: {StepPhase.READY},
    StepPhase.READY: {StepPhase.RUNNING, StepPhase.FAILED},
    StepPhase.RUNNING: {StepPhase.TOOL_REVIEW, StepPhase.TOOL_EXECUTING, StepPhase.SUCCEEDED, StepPhase.FAILED},
    StepPhase.TOOL_REVIEW: {StepPhase.TOOL_EXECUTING, StepPhase.RUNNING, StepPhase.FAILED},
    StepPhase.TOOL_EXECUTING: {StepPhase.OBSERVING, StepPhase.RUNNING, StepPhase.FAILED},
    StepPhase.OBSERVING: {StepPhase.RUNNING, StepPhase.SUCCEEDED, StepPhase.FAILED},
    StepPhase.SUCCEEDED: set(),
    StepPhase.FAILED: set(),
}


def is_step_transition_allowed(source: StepPhase, target: StepPhase) -> bool:
    return target in STEP_PHASE_TRANSITIONS.get(source, set())


def step_phase_transition(source: StepPhase, target: StepPhase) -> StepPhase:
    if not is_step_transition_allowed(source, target):
        from app.core.errors import StateTransitionError

        raise StateTransitionError(source.value, target.value)
    return target


def step_phase_for_status(status) -> StepPhase:
    from app.core.schemas import StepStatus

    mapping = {
        StepStatus.PENDING: StepPhase.PENDING,
        StepStatus.PROPOSED: StepPhase.READY,
        StepStatus.REVIEWED: StepPhase.RUNNING,
        StepStatus.APPROVED: StepPhase.TOOL_EXECUTING,
        StepStatus.RUNNING: StepPhase.TOOL_EXECUTING,
        StepStatus.WAITING_USER_APPROVAL: StepPhase.TOOL_REVIEW,
        StepStatus.SUCCEEDED: StepPhase.SUCCEEDED,
        StepStatus.SKIPPED: StepPhase.SUCCEEDED,
        StepStatus.FAILED: StepPhase.FAILED,
        StepStatus.DENIED: StepPhase.FAILED,
    }
    return mapping.get(status, StepPhase.PENDING)


def set_step_status(step, status, *, actor: str = "StepStateMachine", strict: bool = False):
    target_phase = step_phase_for_status(status)
    source_phase = getattr(step, "step_phase", StepPhase.PENDING) or StepPhase.PENDING
    if source_phase != target_phase and not is_step_transition_allowed(source_phase, target_phase):
        details = {
            "step_id": getattr(step, "id", ""),
            "status": str(getattr(status, "value", status)),
            "from": source_phase.value,
            "to": target_phase.value,
        }
        if strict:
            from app.core.errors import StateTransitionError

            raise StateTransitionError(source_phase.value, target_phase.value)
        record("step.invalid_transition_audited", actor, details, task_id=getattr(step, "task_id", None))
    step.status = status
    step.step_phase = target_phase
    return step
