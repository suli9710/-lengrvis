from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.schemas import AgentAction, MessageType, Plan, PlanStep, StepStatus, Task, ToolResult, new_id
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.step_phase import StepPhase
from app.policy.risk import RiskLevel

OSReflectionAction = Literal["continue", "add_steps", "replace_pending", "ask_user", "finish", "fail"]


@dataclass(slots=True)
class OSReflectionDecision:
    action: OSReflectionAction = "continue"
    reason: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    target_step_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OSReflectionInput:
    task: Task
    plan: Plan
    turn: int
    run_reflection_count: int
    step_reflection_counts: dict[str, int]
    step_outcomes: list[tuple[PlanStep, StepExecutionOutcome]] = field(default_factory=list)
    no_ready: bool = False
    graph_error: str = ""


MAX_REFLECTIONS_PER_RUN = 2
MAX_REFLECTIONS_PER_STEP = 1
PROTECTED_STATUSES = {
    StepStatus.APPROVED,
    StepStatus.SUCCEEDED,
    StepStatus.SKIPPED,
    StepStatus.WAITING_USER_APPROVAL,
    StepStatus.DENIED,
}


class OSReflectionDecider:
    """Small, bounded observe-reflect-decide layer for native OS execution."""

    def __init__(
        self,
        *,
        max_per_run: int = MAX_REFLECTIONS_PER_RUN,
        max_per_step: int = MAX_REFLECTIONS_PER_STEP,
    ) -> None:
        self.max_per_run = max(0, max_per_run)
        self.max_per_step = max(0, max_per_step)

    def should_reflect(self, data: OSReflectionInput) -> bool:
        if data.run_reflection_count >= self.max_per_run:
            return False
        if data.graph_error:
            return True
        if data.no_ready and _pending_mutable_steps(data.plan):
            return True
        for step, outcome in data.step_outcomes:
            if outcome.kind not in {"failed", "fatal_failed"}:
                continue
            if data.step_reflection_counts.get(step.id, 0) >= self.max_per_step:
                continue
            if _result_wants_replan(outcome.result) or _is_low_information_failure(outcome.result):
                return True
        return False

    async def decide(self, data: OSReflectionInput, orchestrator: Any, context: dict[str, Any]) -> OSReflectionDecision:
        if data.graph_error:
            return OSReflectionDecision(action="fail", reason=data.graph_error)

        for step, outcome in data.step_outcomes:
            if outcome.kind not in {"failed", "fatal_failed"}:
                continue
            if data.step_reflection_counts.get(step.id, 0) >= self.max_per_step:
                continue
            result = outcome.result
            if _resource_state_error(result):
                return _read_before_retry_decision(step, result)
            action = await _consult_owner_for_reflection(orchestrator, data.task, step, result)
            if action and action.kind == "propose_tool":
                recovery_step = _step_from_action(data.task, data.plan, step, action, orchestrator)
                return OSReflectionDecision(
                    action="add_steps",
                    reason=action.rationale or f"Add reflected recovery step after {step.tool_name} failed.",
                    steps=[recovery_step],
                    target_step_ids=[step.id],
                )
            if action and action.kind == "request_revision":
                return OSReflectionDecision(
                    action="ask_user",
                    reason=action.follow_up_question or action.rationale or "Reflection needs user clarification.",
                    target_step_ids=[step.id],
                )
            if _is_low_information_failure(result):
                return OSReflectionDecision(
                    action="ask_user",
                    reason=f"{step.tool_name} failed without enough detail for safe automatic replanning.",
                    target_step_ids=[step.id],
                )

        if data.no_ready and _pending_mutable_steps(data.plan):
            return OSReflectionDecision(
                action="ask_user",
                reason="Pending steps are blocked or ambiguous; the plan needs clarification before continuing.",
                target_step_ids=[step.id for step in _pending_mutable_steps(data.plan)],
            )

        return OSReflectionDecision(action="continue", reason="No reflection action needed.")


def apply_reflection_decision(
    task: Task, plan: Plan, decision: OSReflectionDecision, orchestrator: Any
) -> dict[str, Any]:
    if decision.action == "add_steps":
        added = _add_reflection_steps(plan, decision.steps)
        if added:
            _retire_reflected_failed_steps(plan, decision.target_step_ids)
            orchestrator._persist_plan_update(plan, decision.reason or "Added reflected pending step(s).")
            _publish_reflection(orchestrator, task, decision, added_step_ids=[step.id for step in added])
        return {"added_step_ids": [step.id for step in added]}
    if decision.action == "replace_pending":
        replaced = _replace_pending_steps(plan, decision.steps)
        if replaced:
            orchestrator._persist_plan_update(plan, decision.reason or "Replaced pending step(s) after reflection.")
            _publish_reflection(orchestrator, task, decision, added_step_ids=[step.id for step in replaced])
        return {"added_step_ids": [step.id for step in replaced], "replaced_pending": True}
    _publish_reflection(orchestrator, task, decision, added_step_ids=[])
    return {}


def reflection_count_updates(
    state_recovery_counts: dict[str, int],
    decision: OSReflectionDecision,
) -> dict[str, int]:
    updates = dict(state_recovery_counts)
    updates["__os_reflection_run__"] = int(updates.get("__os_reflection_run__", 0)) + 1
    for step_id in decision.target_step_ids:
        updates[step_id] = int(updates.get(step_id, 0)) + 1
    return updates


def _read_before_retry_decision(step: PlanStep, result: ToolResult | None) -> OSReflectionDecision:
    target_path = _path_from_result_or_step(step, result)
    if not target_path:
        return OSReflectionDecision(
            action="ask_user",
            reason="Resource state changed, but no target path was available for a safe read-before-retry step.",
            target_step_ids=[step.id],
        )
    read_step = PlanStep(
        task_id=step.task_id,
        order=step.order + 1,
        agent_name="FileAgent",
        tool_name="file.read_text",
        description=f"Re-read {target_path} before retrying stale write.",
        args={"path": target_path},
        expected_observation="Latest file contents and resource state are captured before another write attempt.",
        risk_level=RiskLevel.R0_READ_ONLY,
        requires_approval=False,
        depends_on=[],
        rollback_strategy="Read-only observation.",
    )
    retry_step = step.model_copy(deep=True)
    retry_step.id = new_id("step")
    retry_step.order = step.order + 2
    retry_step.status = StepStatus.PENDING
    retry_step.depends_on = [read_step.id]
    retry_step.description = f"Retry after resource-state refresh: {step.description}"
    retry_step.requires_approval = bool(step.requires_approval)
    return OSReflectionDecision(
        action="add_steps",
        reason="Observed a resource-state guard failure; re-read the file and retry through ToolRuntime.",
        steps=[read_step, retry_step],
        target_step_ids=[step.id],
    )


async def _consult_owner_for_reflection(
    orchestrator: Any,
    task: Task,
    step: PlanStep,
    result: ToolResult | None,
) -> AgentAction | None:
    if result is None:
        return None
    try:
        action = await orchestrator._consult_subagent(task, step, observation=result)
    except Exception:  # noqa: BLE001 - broad-exception-boundary: reflection is best-effort and must not mask the original failure.
        return None
    return action


def _step_from_action(
    task: Task, plan: Plan, failed_step: PlanStep, action: AgentAction, orchestrator: Any | None = None
) -> PlanStep:  # noqa: ARG001
    tool_name = action.tool_name or failed_step.tool_name
    args = dict(action.args or failed_step.args or {})
    risk = _risk_for_tool(orchestrator, tool_name, failed_step.risk_level)
    agent_name = failed_step.agent_name
    return PlanStep(
        task_id=task.id,
        order=failed_step.order + 1,
        agent_name=agent_name,
        tool_name=tool_name,
        description=action.rationale or f"Reflected recovery for failed step: {failed_step.description}",
        args=args,
        expected_observation=f"Reflected step after {failed_step.id} completes successfully.",
        risk_level=risk,
        requires_approval=failed_step.requires_approval or _risk_requires_approval(risk),
        depends_on=[],
        rollback_strategy=failed_step.rollback_strategy,
    )


def _risk_for_tool(orchestrator: Any | None, tool_name: str, fallback: RiskLevel) -> RiskLevel:
    try:
        return orchestrator.registry.get(tool_name).risk_level
    except Exception:  # noqa: BLE001 - broad-exception-boundary: missing or partial registries should use the failed step risk.
        return fallback


def _risk_requires_approval(risk: RiskLevel) -> bool:
    return risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}


def _add_reflection_steps(plan: Plan, steps: list[PlanStep]) -> list[PlanStep]:
    protected_ids = {step.id for step in plan.steps if step.status in PROTECTED_STATUSES}
    max_order = max((step.order for step in plan.steps), default=0)
    added: list[PlanStep] = []
    existing_ids = {step.id for step in plan.steps}
    existing_signatures = {_reflection_step_signature(step) for step in plan.steps}
    for step in steps:
        if step.id in protected_ids:
            continue
        signature = _reflection_step_signature(step)
        if signature in existing_signatures:
            continue
        if _is_redundant_reflection_step(plan, step):
            continue
        if not step.id or step.id in existing_ids:
            step.id = new_id("step")
        max_order += 1
        step.order = max(step.order, max_order)
        step.status = StepStatus.PENDING
        step.step_phase = StepPhase.PENDING
        plan.steps.append(step)
        existing_ids.add(step.id)
        existing_signatures.add(_reflection_step_signature(step))
        added.append(step)
    return added


def _replace_pending_steps(plan: Plan, steps: list[PlanStep]) -> list[PlanStep]:
    kept = [step for step in plan.steps if step.status != StepStatus.PENDING]
    plan.steps = kept
    return _add_reflection_steps(plan, steps)


def _retire_reflected_failed_steps(plan: Plan, step_ids: list[str]) -> None:
    targets = set(step_ids)
    for step in plan.steps:
        if step.id in targets and step.status == StepStatus.FAILED:
            step.status = StepStatus.SKIPPED
            step.step_phase = StepPhase.SUCCEEDED


def _publish_reflection(
    orchestrator: Any, task: Task, decision: OSReflectionDecision, *, added_step_ids: list[str]
) -> None:
    try:
        orchestrator.bus.publish_text(
            task.id,
            "OSReflectionAgent",
            decision.reason or f"OS reflection decision: {decision.action}",
            message_type=MessageType.REVISION,
            structured_payload={
                "os_reflection": True,
                "action": decision.action,
                "target_step_ids": list(decision.target_step_ids),
                "added_step_ids": added_step_ids,
            },
        )
    except Exception:  # noqa: BLE001 - broad-exception-boundary: event publication must not affect reflection decisions.
        return


def _reflection_step_signature(step: PlanStep) -> tuple[str, str, tuple[str, ...]]:
    return (
        str(step.tool_name or ""),
        _stable_json(step.args or {}),
        tuple(str(item) for item in (step.depends_on or [])),
    )


def _is_redundant_reflection_step(plan: Plan, candidate: PlanStep) -> bool:
    if candidate.depends_on:
        return False
    candidate_args = dict(candidate.args or {})
    for existing in plan.steps:
        if candidate.tool_name != existing.tool_name or existing.depends_on:
            continue
        existing_args = dict(existing.args or {})
        if all(key in existing_args and existing_args[key] == value for key, value in candidate_args.items()):
            return True
    return False


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _pending_mutable_steps(plan: Plan) -> list[PlanStep]:
    return [step for step in plan.steps if step.status == StepStatus.PENDING]


def _result_wants_replan(result: ToolResult | None) -> bool:
    if result is None:
        return True
    output = result.output or {}
    return bool(output.get("replan_recommended") or output.get("resource_state_error"))


def _resource_state_error(result: ToolResult | None) -> bool:
    if result is None:
        return False
    output = result.output or {}
    return bool(
        output.get("resource_state_error")
        or output.get("error_code") in {"STALE_RESOURCE_STATE", "READ_STATE_REQUIRED"}
    )


def _is_low_information_failure(result: ToolResult | None) -> bool:
    if result is None:
        return True
    text = " ".join(
        str(value or "")
        for value in (result.error, result.observation, result.output.get("error") if result.output else "")
    )
    normalized = text.strip().casefold()
    return not normalized or normalized in {"planned failure", "tool failed.", "failed", "unknown error"}


def _path_from_result_or_step(step: PlanStep, result: ToolResult | None) -> str:
    output = result.output if result is not None else {}
    for collection_key in ("missing_read_state", "stale_read_state", "_resource_state_before"):
        values = output.get(collection_key) if isinstance(output, dict) else None
        if isinstance(values, list):
            for item in values:
                if isinstance(item, dict) and item.get("path"):
                    return str(item["path"])
    for key in ("path", "source", "target", "destination"):
        value = step.args.get(key)
        if value:
            return str(value)
    return ""
