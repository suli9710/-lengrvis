from __future__ import annotations

from enum import StrEnum


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
