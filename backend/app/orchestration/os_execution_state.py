from __future__ import annotations

from typing import Any

from app.core.audit import record
from app.core.schemas import Plan, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.orchestration.execution_models import LargeResultRef, RunObservation, RunPhase, RunState
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.observations import summarize_result
from app.orchestration.tool_execution_journal import load_persisted_observations
from app.policy.risk import RiskLevel

TERMINAL_STEP_STATUSES = {
    StepStatus.SUCCEEDED,
    StepStatus.SKIPPED,
    StepStatus.FAILED,
    StepStatus.DENIED,
    StepStatus.WAITING_USER_APPROVAL,
}


def stop_outcome(step_outcomes: list[tuple[PlanStep, StepExecutionOutcome]]) -> str:
    kinds = {outcome.kind for _step, outcome in step_outcomes}
    if "waiting_user_approval" in kinds:
        return "waiting_approval"
    if "revision_requested" in kinds:
        return "paused"
    if kinds & {"step_denied", "fatal_denied"}:
        return "denied"
    if kinds & {"fatal_failed"}:
        return "failed"
    return "continue"


def pending_step_ids(plan: Plan) -> set[str]:
    return {step.id for step in plan.steps if step.status not in TERMINAL_STEP_STATUSES}


def observations_by_step(state: RunState, *, orchestrator_name: str) -> dict[str, ToolResult]:
    observations: dict[str, ToolResult] = {}
    for observation in state.observations:
        step_id = str(observation.payload.get("step_id") or "")
        result_payload = observation.payload.get("tool_result")
        if not step_id or not isinstance(result_payload, dict):
            continue
        try:
            observations[step_id] = ToolResult.model_validate(result_payload)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            record(
                "run.observation_payload_invalid",
                orchestrator_name,
                {"step_id": step_id, "error": str(exc)},
                task_id=state.task_id,
            )
            continue
    if state.task_id:
        observations.update(load_persisted_observations(state.task_id))
    return observations


def run_observation(turn: int, step: PlanStep, outcome: StepExecutionOutcome) -> RunObservation:
    result = outcome.result
    if result is None:
        raise ValueError("Run observation requires a tool result.")
    return RunObservation(
        turn=turn,
        source=step.agent_name or "ToolRuntime",
        message=summarize_result(result),
        payload={
            "step_id": step.id,
            "tool_name": step.tool_name,
            "outcome": outcome.kind,
            "tool_result": result.model_dump(mode="json"),
        },
    )


def large_result_ref(result: ToolResult) -> LargeResultRef | None:
    output = result.output or {}
    if not output.get("persisted_result"):
        return None
    return LargeResultRef(
        ref_id=result.id,
        path=str(output.get("path") or ""),
        original_size=int(output.get("original_size") or 0),
        preview=str(output.get("preview") or ""),
        has_more=bool(output.get("has_more")),
    )


def state_from_task_plan(
    state: RunState,
    task: Task,
    plan: Plan,
    *,
    phase: RunPhase,
    reason: str,
    turn_count: int | None = None,
) -> RunState:
    return state.model_copy(
        update={
            "phase": phase,
            "turn_count": state.turn_count if turn_count is None else turn_count,
            "transition_reason": reason,
            "current_plan": plan_snapshot(task, plan),
            "goal": task.user_goal or state.goal,
            "mode": task.mode or state.mode,
            "task_id": task.id,
            "paused": phase == RunPhase.PAUSED,
        },
        deep=True,
    )


def plan_snapshot(task: Task, plan: Plan) -> dict[str, Any]:
    return {
        "task_id": task.id,
        "task_status": str(task.status.value if hasattr(task.status, "value") else task.status),
        "execution_stage": str(
            task.execution_stage.value if hasattr(task.execution_stage, "value") else task.execution_stage
        ),
        "plan_id": plan.id,
        "goal": plan.goal,
        "step_status_counts": step_status_counts(plan),
        "steps": [step.model_dump(mode="json") for step in plan.steps],
    }


def step_status_counts(plan: Plan) -> dict[str, int]:
    counts: dict[str, int] = {}
    for step in plan.steps:
        key = step.status.value if hasattr(step.status, "value") else str(step.status)
        counts[key] = counts.get(key, 0) + 1
    return counts


def recent_failure_count(plan: Plan) -> int:
    return sum(1 for step in plan.steps if step.status == StepStatus.FAILED)


def step_outcome_payload(step: PlanStep, outcome: StepExecutionOutcome) -> dict[str, Any]:
    return {
        "step_id": step.id,
        "tool_name": step.tool_name,
        "kind": outcome.kind,
        "status": step.status.value if hasattr(step.status, "value") else str(step.status),
        "result_id": outcome.result.id if outcome.result is not None else "",
    }


def phase_for_task(task: Task) -> RunPhase:
    if task.execution_stage == ExecutionStage.AWAITING_APPROVAL:
        return RunPhase.AWAITING_APPROVAL
    if task.execution_stage == ExecutionStage.PAUSED:
        return RunPhase.PAUSED
    if task.status == TaskStatus.FAILED:
        return RunPhase.FAILED
    if task.status == TaskStatus.CANCELLED:
        return RunPhase.CANCELLED
    if task.status == TaskStatus.DENIED:
        return RunPhase.DENIED
    if task.status == TaskStatus.COMPLETED:
        return RunPhase.COMPLETED
    return RunPhase.RUNNING


def phase_for_task_plan(task: Task, plan: Plan) -> RunPhase:
    phase = phase_for_task(task)
    if phase != RunPhase.CANCELLED:
        return phase
    summary = (task.final_summary or "").casefold()
    if "cancel" in summary or "rejected" in summary:
        return RunPhase.CANCELLED
    if "deny" in summary or "denied" in summary or "forbidden" in summary or "safety" in summary:
        return RunPhase.DENIED
    if plan.global_risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF:
        return RunPhase.DENIED
    if any(step.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF for step in plan.steps):
        return RunPhase.DENIED
    if any(step.status == StepStatus.DENIED for step in plan.steps):
        return RunPhase.DENIED
    return RunPhase.CANCELLED


def event_name_for_outcome(outcome: str) -> str:
    return {
        "cancelled": "run.cancelled",
        "waiting_approval": "run.waiting_approval",
        "completed": "run.completed",
        "failed": "run.failed",
        "denied": "run.denied",
        "paused": "run.paused",
    }.get(outcome, "")
