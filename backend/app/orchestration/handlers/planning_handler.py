from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from app.agents.delegation_metadata import (
    SupervisorHintPlanError,
    is_memory_non_persistence_goal,
    plan_matches_supervisor_hint,
    plan_tools_outside_visible,
)
from app.agents.worker_agents import normalize_supervisor_agent_hint
from app.core import db
from app.core.audit import record
from app.core.schemas import MessageType, Plan, StepStatus, Task, TaskStatus
from app.orchestration.deterministic_contracts import (
    DETERMINISTIC_PLAN_CREATOR,
    seal_deterministic_plan,
)
from app.orchestration.step_phase import set_step_status
from app.perception.context_store import latest_perception_context
from app.perception.storage import perception_context_summary
from app.policy.model_boundary import ModelActionEnvelope, model_control_arg_error
from app.policy.risk import RiskLevel, SafetyVerdict

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher


def _filter_planner_kwargs(create_plan, optional_kwargs: dict) -> dict:
    """Keep only the keyword arguments the planner's ``create_plan`` accepts.

    Legacy planners (older signatures without ``tool_specs``/``agent_hint``/
    perception kwargs) are supported by signature introspection instead of
    fragile TypeError string sniffing. Planners exposing ``**kwargs`` (or
    uninspectable callables such as mocks) receive everything.
    """
    try:
        parameters = inspect.signature(create_plan).parameters
    except (TypeError, ValueError):
        return dict(optional_kwargs)
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
        return dict(optional_kwargs)
    return {name: value for name, value in optional_kwargs.items() if name in parameters}


def _planner_tool_spec(tool) -> str:
    """One prompt line per tool: name, description, and required args.

    Falls back to the bare name when the description is the generated
    placeholder, so the planner prompt never shows redundant text.
    """
    name = str(getattr(tool, "name", "") or "")
    spec = name
    description = str(getattr(tool, "description", "") or "").strip()
    if description and description != name.replace(".", " "):
        spec = f"{name}: {description}"
    schema = getattr(tool, "input_schema", None)
    required = schema.get("required") if isinstance(schema, dict) else None
    if required:
        spec += f" (required: {', '.join(str(item) for item in required)})"
    return spec


class PlanningHandler:
    def __init__(self, orchestrator: OrchestratorAgent) -> None:
        self.orchestrator = orchestrator

    def register(self, _dispatcher: EventDispatcher) -> None:
        """Compatibility no-op.

        Planning runs through ``run_task`` and direct orchestrator calls; the
        dispatcher is not the production workflow engine.
        """
        return None

    async def run_task(self, task: Task) -> Task:
        orchestrator = self.orchestrator
        goal = task.user_goal
        mode = task.mode
        if not orchestrator._supervise_new_agent_messages(task.id, "user_goal"):
            return orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task during initial runtime supervision.",
            )

        goal_review = orchestrator.safety.review_goal(task.id, goal)
        if goal_review.verdict == SafetyVerdict.DENY:
            return orchestrator._set_status(task, TaskStatus.DENIED, final_summary=goal_review.safe_alternative)

        memory_context = await orchestrator._recall_memory(goal)
        goal_context = self._goal_context_for_planning(task, goal)
        session_context = self._session_context_for_planning(task)
        agent_hint = normalize_supervisor_agent_hint((task.metadata or {}).get("supervisor_agent_hint")) or None
        try:
            plan = await self._create_plan(
                task, goal, mode, memory_context, goal_context, session_context, agent_hint=agent_hint
            )
        except SupervisorHintPlanError as exc:
            record(
                "planner.supervisor_hint_denied",
                "PlanningHandler",
                {"error": str(exc), "agent_hint": agent_hint or ""},
                task_id=task.id,
            )
            return orchestrator._set_status(task, TaskStatus.FAILED, final_summary=str(exc))
        db.upsert_model("plans", plan)
        if not orchestrator._supervise_new_agent_messages(task.id, "planner_output"):
            return orchestrator._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task after PlannerAgent output.",
            )

        plan_review = orchestrator.consultation_handler.consult_and_review(task, plan)
        if plan_review.verdict == SafetyVerdict.DENY:
            self._mark_denied_plan_steps(plan)
            db.upsert_model("plans", plan)
            summary = plan_review.safe_alternative or "; ".join(plan_review.reasons) or "Plan denied by safety review."
            return orchestrator._set_status(task, TaskStatus.DENIED, final_summary=f"Denied: {summary}")

        await orchestrator._process_steps(task, plan)
        await orchestrator.completion_handler.finalize(task, plan)
        return task

    async def _create_plan(
        self,
        task: Task,
        goal: str,
        mode: str,
        memory_context: list,
        goal_context: dict | None = None,
        session_context: dict | None = None,
        *,
        agent_hint: str | None = None,
    ) -> Plan:
        orchestrator = self.orchestrator
        list_tools = getattr(orchestrator.registry, "list_for_planning", orchestrator.registry.list)
        visible_tools = [
            tool for tool in list_tools() if tool.name == "tool.search" or not getattr(tool, "defer_loading", False)
        ]
        memory_non_persistence = is_memory_non_persistence_goal(goal)
        if memory_non_persistence:
            visible_tools = [tool for tool in visible_tools if not tool.name.startswith("memory.")]
        hint = normalize_supervisor_agent_hint(agent_hint)
        if hint:
            hinted_tools = [
                tool for tool in visible_tools if tool.name == "tool.search" or getattr(tool, "agent_owner", "") == hint
            ]
            visible_tools = hinted_tools or [tool for tool in visible_tools if tool.name == "tool.search"]
        tools = [tool.name for tool in visible_tools]
        tool_specs = [_planner_tool_spec(tool) for tool in visible_tools]
        perception_context = perception_context_summary(latest_perception_context())
        create_plan = orchestrator.planner.create_plan
        base_planner_kwargs = {
            "memory_context": memory_context,
            "perception_context": perception_context,
            "goal_context": goal_context,
            "session_context": session_context,
            "tool_specs": tool_specs,
            "agent_hint": agent_hint,
        }
        plan: Plan | None = None
        last_outside: list[str] = []
        attempts = 2 if hint else 1
        for attempt in range(attempts):
            planner_kwargs = dict(base_planner_kwargs)
            if attempt > 0 and last_outside:
                planner_kwargs["planner_revision_feedback"] = (
                    f"Previous plan used tools outside the allowed surface for {hint}: "
                    f"{', '.join(last_outside)}. "
                    f"Regenerate using only these tools: {', '.join(tools)}."
                )
            planner_kwargs = _filter_planner_kwargs(create_plan, planner_kwargs)
            plan = await create_plan(task.id, goal, mode, tools, **planner_kwargs)
            if memory_non_persistence:
                blocked_memory_steps = [step.tool_name for step in plan.steps if step.tool_name.startswith("memory.")]
                if blocked_memory_steps:
                    plan.steps = [step for step in plan.steps if not step.tool_name.startswith("memory.")]
                    record(
                        "planner.memory_non_persistence_enforced",
                        "PlanningHandler",
                        {"blocked_tools": blocked_memory_steps},
                        task_id=task.id,
                    )
            self._annotate_plan_tool_contracts(plan, tools)
            if not hint:
                break
            last_outside = plan_tools_outside_visible(plan, tools)
            if not last_outside and plan_matches_supervisor_hint(plan, hint, tools):
                break
            record(
                "planner.supervisor_hint_retry",
                "PlanningHandler",
                {"attempt": attempt + 1, "outside_tools": last_outside, "agent_hint": hint},
                task_id=task.id,
            )
        assert plan is not None  # noqa: S101
        plan = self._guard_supervisor_hint_plan(task.id, plan, tools, hint, last_outside)
        self._publish_annotated_plan(task.id, plan)
        return plan

    def _guard_supervisor_hint_plan(
        self,
        task_id: str,
        plan: Plan,
        tools: list[str],
        agent_hint: str | None,
        outside_tools: list[str],
    ) -> Plan:
        hint = normalize_supervisor_agent_hint(agent_hint)
        if not hint:
            return plan
        had_steps = bool(plan.steps)
        stripped = outside_tools or plan_tools_outside_visible(plan, tools)
        if stripped:
            allowed = set(tools)
            plan.steps = [step for step in plan.steps if step.tool_name in allowed]
            record(
                "planner.supervisor_hint_stripped",
                "PlanningHandler",
                {"outside_tools": stripped, "agent_hint": hint},
                task_id=task_id,
            )
        if not plan.steps:
            # A plan that was empty from the start is a legitimate
            # clarification-style reply and must not hard-fail the hint
            # (consistent with plan_matches_supervisor_hint). Only a plan
            # emptied by stripping out-of-surface tools is a violation.
            if not had_steps:
                return plan
            raise SupervisorHintPlanError(f"Supervisor hint {hint} could not be satisfied by the generated plan.")
        if not plan_matches_supervisor_hint(plan, hint, tools):
            raise SupervisorHintPlanError(f"Supervisor hint {hint} could not be satisfied by the generated plan.")
        return plan

    def _annotate_plan_tool_contracts(self, plan: Plan, visible_tool_ids: list[str]) -> None:
        for step in plan.steps:
            model_supplied_risk = getattr(step.risk_level, "value", str(step.risk_level or ""))
            try:
                tool = self.orchestrator.registry.get(step.tool_name)
            except KeyError:
                step.model_action = self._model_action_for_step(
                    step, visible_tool_ids, model_supplied_risk=model_supplied_risk
                )
                continue

            step.agent_name = getattr(tool, "agent_owner", "") or step.agent_name
            step.risk_level = tool.risk_level
            step.requires_approval = tool.risk_level in {
                RiskLevel.R2_REVERSIBLE_MODIFY,
                RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            }
            if step.requires_approval and "dry_run" not in step.args:
                step.args = {**dict(step.args or {}), "dry_run": True}
            step.tool_effects = list(getattr(tool, "effects", []) or [])
            step.resource_kinds = list(getattr(tool, "resource_kinds", []) or [])
            step.trust_tier = str(getattr(tool, "trust_tier", "") or "")
            step.deferred_tool = bool(getattr(tool, "defer_loading", False))
            step.model_action = self._model_action_for_step(
                step,
                visible_tool_ids,
                model_supplied_risk=model_supplied_risk,
                runtime_metadata={
                    "derived_risk_level": tool.risk_level.value,
                    "tool_version": str(getattr(tool, "tool_version", "1") or "1"),
                    "tool_contract_valid": bool(getattr(tool, "is_model_visible", lambda: False)()),
                },
            )
        if plan.steps:
            from app.policy.risk import max_risk

            plan.global_risk_level = max_risk([step.risk_level for step in plan.steps])
            plan.requires_user_approval = any(step.requires_approval for step in plan.steps)
        if plan.created_by_agent == DETERMINISTIC_PLAN_CREATOR:
            # Annotation may normalize risk, add dry-run args and replace the
            # model-action envelope. Bind the final exact call after those
            # changes so later model-driven layers cannot rewrite it.
            seal_deterministic_plan(plan)

    def _publish_annotated_plan(self, task_id: str, plan: Plan) -> None:
        publish_text = getattr(getattr(self.orchestrator, "bus", None), "publish_text", None)
        if not callable(publish_text):
            return
        publish_text(
            task_id,
            "PlannerAgent",
            f"Annotated plan contracts for {len(plan.steps)} step(s).",
            message_type=MessageType.REVISION,
            structured_payload=plan.model_dump(mode="json"),
            metadata={"event_type": "plan.contract_annotated", "boundary": "tool_contract"},
        )

    def _mark_denied_plan_steps(self, plan: Plan) -> None:
        for step in plan.steps:
            if step.risk_level == RiskLevel.R4_FORBIDDEN_OR_HANDOFF and step.status == StepStatus.PENDING:
                set_step_status(step, StepStatus.DENIED, actor="PlanningHandler")

    def _model_action_for_step(
        self,
        step,
        visible_tool_ids: list[str],
        *,
        model_supplied_risk: str,
        runtime_metadata: dict | None = None,
    ) -> dict:
        metadata = {
            "model_supplied_risk_level": model_supplied_risk,
            "boundary_error": model_control_arg_error(step.args),
            **(runtime_metadata or {}),
        }
        return ModelActionEnvelope(
            action_type="plan_step",
            tool_name=step.tool_name,
            args=dict(step.args or {}),
            model_reason=step.description,
            visible_tool_ids=list(visible_tool_ids),
            source="PlannerAgent",
            task_id=step.task_id,
            step_id=step.id,
            runtime_metadata=metadata,
        ).to_metadata()

    def _goal_context_for_planning(self, task: Task, goal: str) -> dict | None:
        goal_stack = getattr(self.orchestrator, "goal_stack", None)
        if goal_stack is None:
            return None
        try:
            related_goal = goal_stack.find_related(goal)
            if related_goal is None:
                goal_stack.push(goal, task_id=task.id, parent_goal_id="")
            else:
                goal_stack.relate_task(task.id, related_goal.id)
            return goal_stack.get_context_for_planning(goal)
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            record(
                "goal_stack.context_failed",
                self.orchestrator.name,
                {"task_id": task.id, "error": str(exc)},
                task_id=task.id,
            )
            return None

    def _session_context_for_planning(self, task: Task) -> dict | None:
        store = getattr(self.orchestrator, "session_context_store", None)
        if store is None:
            return None
        try:
            store.remember_task(task.id, workflow_state={"latest_goal": task.user_goal, "latest_task_id": task.id})
            return store.planning_context()
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            record(
                "session_context.planning_context_failed",
                self.orchestrator.name,
                {"task_id": task.id, "error": str(exc)},
                task_id=task.id,
            )
            return None
