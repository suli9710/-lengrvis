from __future__ import annotations

from app.core import db
from app.core.audit import record
from app.core.errors import StateTransitionError
from app.core.schemas import Task, TaskStatus, now_iso
from app.llm.registry import get_effective_settings


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.PLANNING, TaskStatus.CANCELLED, TaskStatus.DENIED},
    TaskStatus.PLANNING: {TaskStatus.REVIEWING_PLAN, TaskStatus.AGENT_CONSULTATION, TaskStatus.DENIED, TaskStatus.FAILED},
    TaskStatus.REVIEWING_PLAN: {
        TaskStatus.AGENT_CONSULTATION,
        TaskStatus.WAITING_USER_APPROVAL,
        TaskStatus.EXECUTING_STEP,
        TaskStatus.COMPLETED,
        TaskStatus.DENIED,
        TaskStatus.FAILED,
    },
    TaskStatus.AGENT_CONSULTATION: {
        TaskStatus.REVIEWING_PLAN,
        TaskStatus.PLAN_FINAL_REVIEW,
        TaskStatus.EXECUTING_STEP,
        TaskStatus.DENIED,
        TaskStatus.FAILED,
    },
    TaskStatus.PLAN_FINAL_REVIEW: {
        TaskStatus.EXECUTING_STEP,
        TaskStatus.WAITING_USER_APPROVAL,
        TaskStatus.COMPLETED,
        TaskStatus.DENIED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_USER_APPROVAL: {
        TaskStatus.EXECUTING_STEP,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.DENIED,
    },
    TaskStatus.EXECUTING_STEP: {
        TaskStatus.REVIEWING_TOOL_CALL,
        TaskStatus.EXECUTING_TOOL,
        TaskStatus.FINAL_REVIEW,
        TaskStatus.WAITING_USER_APPROVAL,
        TaskStatus.COMPLETED,
        TaskStatus.DENIED,
        TaskStatus.FAILED,
    },
    TaskStatus.REVIEWING_TOOL_CALL: {
        TaskStatus.EXECUTING_TOOL,
        TaskStatus.EXECUTING_STEP,
        TaskStatus.WAITING_USER_APPROVAL,
        TaskStatus.DENIED,
        TaskStatus.FAILED,
    },
    TaskStatus.EXECUTING_TOOL: {
        TaskStatus.RECORDING_OBSERVATION,
        TaskStatus.EXECUTING_STEP,
        TaskStatus.WAITING_USER_APPROVAL,
        TaskStatus.COMPLETED,
        TaskStatus.DENIED,
        TaskStatus.FAILED,
    },
    TaskStatus.RECORDING_OBSERVATION: {
        TaskStatus.AGENT_DISCUSSION,
        TaskStatus.EXECUTING_STEP,
        TaskStatus.DENIED,
        TaskStatus.FAILED,
    },
    TaskStatus.AGENT_DISCUSSION: {
        TaskStatus.REVIEWING_NEXT_STEP,
        TaskStatus.EXECUTING_STEP,
        TaskStatus.DENIED,
        TaskStatus.FAILED,
    },
    TaskStatus.REVIEWING_NEXT_STEP: {
        TaskStatus.EXECUTING_STEP,
        TaskStatus.FINAL_REVIEW,
        TaskStatus.DENIED,
    },
    TaskStatus.FINAL_REVIEW: {
        TaskStatus.COMPLETED,
        TaskStatus.DENIED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PAUSED: {TaskStatus.EXECUTING_STEP, TaskStatus.CANCELLED, TaskStatus.DENIED},
    TaskStatus.COMPLETED: {TaskStatus.ROLLED_BACK},
    TaskStatus.FAILED: {TaskStatus.ROLLED_BACK},
    TaskStatus.DENIED: set(),
    TaskStatus.CANCELLED: set(),
    TaskStatus.ROLLED_BACK: set(),
}


def is_transition_allowed(source: TaskStatus, target: TaskStatus, *, strict: bool = False) -> bool:
    if target in ALLOWED_TRANSITIONS.get(source, set()):
        return True
    if strict:
        return False
    return source in {
        TaskStatus.CREATED,
        TaskStatus.FAILED,
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
    }


def _invalid_transition_error(source: TaskStatus, target: TaskStatus) -> StateTransitionError:
    return StateTransitionError(source.value, target.value)


def transition(task: Task, target: TaskStatus, actor: str = "StateMachine") -> Task:
    if not is_transition_allowed(task.status, target):
        raise _invalid_transition_error(task.status, target)
    old = task.status
    task.status = target
    task.updated_at = now_iso()
    db.upsert_model("tasks", task)
    record("task.status_changed", actor, {"from": old, "to": target}, task_id=task.id)
    return task


def ensure_transition_allowed(task: Task, target: TaskStatus) -> None:
    if not is_transition_allowed(task.status, target, strict=True):
        raise _invalid_transition_error(task.status, target)


def safe_transition(task: Task, target: TaskStatus, actor: str = "StateMachine") -> Task:
    """Same as transition but degrades gracefully on illegal transitions.

    Records an audit entry, then forces the status anyway so the orchestrator can
    keep moving. Designed for the orchestrator's main loop where we never want a
    pedantic transition table to lose the user's task.
    """
    old = task.status
    if target == old:
        task.updated_at = now_iso()
        db.upsert_model("tasks", task)
        return task
    strict = get_effective_settings().strict_state_machine
    if strict and not is_transition_allowed(old, target, strict=True):
        raise _invalid_transition_error(old, target)
    try:
        return transition(task, target, actor=actor)
    except StateTransitionError as exc:
        if strict:
            raise
        record(
            "task.invalid_transition",
            actor,
            {"from": old, "to": target, "error": str(exc)},
            task_id=task.id,
        )
        task.status = target
        task.updated_at = now_iso()
        db.upsert_model("tasks", task)
        record("task.status_changed", actor, {"from": old, "to": target, "forced": True}, task_id=task.id)
        return task
