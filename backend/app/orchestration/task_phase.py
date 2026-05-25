from __future__ import annotations

from enum import StrEnum


class TaskPhase(StrEnum):
    CREATED = "created"
    GOAL_ANALYSIS = "goal_analysis"
    PLANNING = "planning"
    CONSULTATION = "consultation"
    PLAN_REVIEW = "plan_review"
    EXECUTION = "execution"
    FINAL_REVIEW = "final_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TASK_PHASE_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.CREATED: {TaskPhase.GOAL_ANALYSIS, TaskPhase.PLANNING, TaskPhase.CANCELLED},
    TaskPhase.GOAL_ANALYSIS: {TaskPhase.PLANNING, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.PLANNING: {TaskPhase.CONSULTATION, TaskPhase.PLAN_REVIEW, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.CONSULTATION: {TaskPhase.PLAN_REVIEW, TaskPhase.PLANNING, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.PLAN_REVIEW: {TaskPhase.EXECUTION, TaskPhase.PLANNING, TaskPhase.CONSULTATION, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.EXECUTION: {TaskPhase.FINAL_REVIEW, TaskPhase.COMPLETED, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.FINAL_REVIEW: {TaskPhase.COMPLETED, TaskPhase.EXECUTION, TaskPhase.FAILED, TaskPhase.CANCELLED},
    TaskPhase.COMPLETED: set(),
    TaskPhase.FAILED: set(),
    TaskPhase.CANCELLED: set(),
}


def is_phase_transition_allowed(source: TaskPhase, target: TaskPhase) -> bool:
    return target in TASK_PHASE_TRANSITIONS.get(source, set())


def phase_transition(source: TaskPhase, target: TaskPhase) -> TaskPhase:
    if not is_phase_transition_allowed(source, target):
        from app.core.errors import StateTransitionError

        raise StateTransitionError(source.value, target.value)
    return target
