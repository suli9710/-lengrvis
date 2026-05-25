from __future__ import annotations

from enum import StrEnum


class ExecutionStage(StrEnum):
    IDLE = "idle"
    STEP_RUNNING = "step_running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"


EXECUTION_STAGE_TRANSITIONS: dict[ExecutionStage, set[ExecutionStage]] = {
    ExecutionStage.IDLE: {ExecutionStage.STEP_RUNNING},
    ExecutionStage.STEP_RUNNING: {ExecutionStage.AWAITING_APPROVAL, ExecutionStage.IDLE, ExecutionStage.PAUSED},
    ExecutionStage.AWAITING_APPROVAL: {ExecutionStage.STEP_RUNNING, ExecutionStage.IDLE, ExecutionStage.PAUSED},
    ExecutionStage.PAUSED: {ExecutionStage.STEP_RUNNING, ExecutionStage.IDLE},
}


def is_stage_transition_allowed(source: ExecutionStage, target: ExecutionStage) -> bool:
    return target in EXECUTION_STAGE_TRANSITIONS.get(source, set())


def stage_transition(source: ExecutionStage, target: ExecutionStage) -> ExecutionStage:
    if not is_stage_transition_allowed(source, target):
        from app.core.errors import StateTransitionError

        raise StateTransitionError(source.value, target.value)
    return target
