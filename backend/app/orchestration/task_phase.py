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
    DENIED = "denied"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"
    REPAIR_REQUIRED = "repair_required"


TASK_PHASE_TRANSITIONS: dict[TaskPhase, set[TaskPhase]] = {
    TaskPhase.CREATED: {
        TaskPhase.GOAL_ANALYSIS,
        TaskPhase.PLANNING,
        TaskPhase.FAILED,
        TaskPhase.DENIED,
        TaskPhase.CANCELLED,
    },
    TaskPhase.GOAL_ANALYSIS: {TaskPhase.PLANNING, TaskPhase.FAILED, TaskPhase.DENIED, TaskPhase.CANCELLED},
    TaskPhase.PLANNING: {
        TaskPhase.CONSULTATION,
        TaskPhase.PLAN_REVIEW,
        TaskPhase.FAILED,
        TaskPhase.DENIED,
        TaskPhase.CANCELLED,
    },
    TaskPhase.CONSULTATION: {
        TaskPhase.PLAN_REVIEW,
        TaskPhase.PLANNING,
        TaskPhase.FAILED,
        TaskPhase.DENIED,
        TaskPhase.CANCELLED,
    },
    TaskPhase.PLAN_REVIEW: {
        TaskPhase.EXECUTION,
        TaskPhase.PLANNING,
        TaskPhase.CONSULTATION,
        TaskPhase.FAILED,
        TaskPhase.DENIED,
        TaskPhase.CANCELLED,
    },
    TaskPhase.EXECUTION: {
        TaskPhase.FINAL_REVIEW,
        TaskPhase.COMPLETED,
        TaskPhase.FAILED,
        TaskPhase.DENIED,
        TaskPhase.CANCELLED,
    },
    TaskPhase.FINAL_REVIEW: {
        TaskPhase.COMPLETED,
        TaskPhase.EXECUTION,
        TaskPhase.FAILED,
        TaskPhase.DENIED,
        TaskPhase.CANCELLED,
    },
    # Completion is persisted before the final safety review today. Keep the
    # review able to replace it with the stronger terminal safety outcome.
    TaskPhase.COMPLETED: {TaskPhase.DENIED, TaskPhase.ROLLED_BACK, TaskPhase.REPAIR_REQUIRED},
    TaskPhase.FAILED: {TaskPhase.ROLLED_BACK, TaskPhase.REPAIR_REQUIRED},
    TaskPhase.DENIED: set(),
    TaskPhase.CANCELLED: set(),
    TaskPhase.ROLLED_BACK: set(),
    TaskPhase.REPAIR_REQUIRED: set(),
}


TERMINAL_TASK_PHASES: frozenset[TaskPhase] = frozenset(
    {
        TaskPhase.COMPLETED,
        TaskPhase.FAILED,
        TaskPhase.DENIED,
        TaskPhase.CANCELLED,
        TaskPhase.ROLLED_BACK,
        TaskPhase.REPAIR_REQUIRED,
    }
)


def is_phase_transition_allowed(source: TaskPhase, target: TaskPhase) -> bool:
    return target in TASK_PHASE_TRANSITIONS.get(source, set())


def phase_transition(source: TaskPhase, target: TaskPhase) -> TaskPhase:
    if not is_phase_transition_allowed(source, target):
        from app.core.errors import StateTransitionError

        raise StateTransitionError(source.value, target.value)
    return target
