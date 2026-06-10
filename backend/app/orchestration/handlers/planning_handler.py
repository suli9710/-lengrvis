from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.audit import record
from app.core import db
from app.core.schemas import MessageType, Plan, StepStatus, Task, TaskStatus
from app.perception.context_store import latest_perception_context
from app.perception.storage import perception_context_summary
from app.policy.model_boundary import ModelActionEnvelope, model_control_arg_error
from app.policy.risk import SafetyVerdict
from app.policy.risk import RiskLevel
from app.orchestration.step_phase import set_step_status

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher


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
        plan = await self._create_plan(task, goal, mode, memory_context, goal_context, session_context)
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
    ) -> Plan:
        orchestrator = self.orchestrator
        list_tools = getattr(orchestrator.registry, "list_for_planning", orchestrator.registry.list)
        visible_tools = [tool for tool in list_tools() if tool.name == "tool.search" or not getattr(tool, "defer_loading", False)]
        tools = [tool.name for tool in visible_tools]
        tool_specs = [_planner_tool_spec(tool) for tool in visible_tools]
        perception_context = perception_context_summary(latest_perception_context())
        try:
            plan = await orchestrator.planner.create_plan(
                task.id,
                goal,
                mode,
                tools,
                memory_context=memory_context,
                perception_context=perception_context,
                goal_context=goal_context,
                session_context=session_context,
                tool_specs=tool_specs,
            )
            self._annotate_plan_tool_contracts(plan, tools)
            self._publish_annotated_plan(task.id, plan)
            return plan
        except TypeError as exc:
            if "tool_specs" in str(exc):
                # Planner predates tool_specs: drop only that kwarg first so
                # session/goal/perception context still reaches it.
                try:
                    plan = await orchestrator.planner.create_plan(
                        task.id,
                        goal,
                        mode,
                        tools,
                        memory_context=memory_context,
                        perception_context=perception_context,
                        goal_context=goal_context,
                        session_context=session_context,
                    )
                    self._annotate_plan_tool_contracts(plan, tools)
                    self._publish_annotated_plan(task.id, plan)
                    return plan
                except TypeError as retry_exc:
                    exc = retry_exc
            if (
                "perception_context" not in str(exc)
                and "goal_context" not in str(exc)
                and "session_context" not in str(exc)
            ):
                raise
            try:
                plan = await orchestrator.planner.create_plan(
                    task.id,
                    goal,
                    mode,
                    tools,
                    memory_context=memory_context,
                    perception_context=perception_context,
                    goal_context=goal_context,
                )
                self._annotate_plan_tool_contracts(plan, tools)
                self._publish_annotated_plan(task.id, plan)
                return plan
            except TypeError as inner_exc:
                if "perception_context" not in str(inner_exc) and "goal_context" not in str(inner_exc):
                    raise
                plan = await orchestrator.planner.create_plan(
                    task.id,
                    goal,
                    mode,
                    tools,
                    memory_context=memory_context,
                )
                self._annotate_plan_tool_contracts(plan, tools)
                self._publish_annotated_plan(task.id, plan)
                return plan

    def _annotate_plan_tool_contracts(self, plan: Plan, visible_tool_ids: list[str]) -> None:
        for step in plan.steps:
            model_supplied_risk = getattr(step.risk_level, "value", str(step.risk_level or ""))
            try:
                tool = self.orchestrator.registry.get(step.tool_name)
            except KeyError:
                step.model_action = self._model_action_for_step(step, visible_tool_ids, model_supplied_risk=model_supplied_risk)
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
        except Exception as exc:
            record("goal_stack.context_failed", self.orchestrator.name, {"task_id": task.id, "error": str(exc)}, task_id=task.id)
            return None

    def _session_context_for_planning(self, task: Task) -> dict | None:
        store = getattr(self.orchestrator, "session_context_store", None)
        if store is None:
            return None
        try:
            store.remember_task(task.id, workflow_state={"latest_goal": task.user_goal, "latest_task_id": task.id})
            return store.planning_context()
        except Exception as exc:
            record("session_context.planning_context_failed", self.orchestrator.name, {"task_id": task.id, "error": str(exc)}, task_id=task.id)
            return None
