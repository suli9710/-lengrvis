from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.audit import record
from app.core.schemas import AgentAction, MessageType, Plan, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.orchestration.events import StepEvent, ToolFailed
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.step_phase import set_step_status
from app.tools import rollback_tools

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher


DEFAULT_RECOVERY_MAX_RETRIES = 3


class RecoveryHandler:
    """Recover from tool failures without pushing fallback logic into the scheduler."""

    def __init__(self, orchestrator: OrchestratorAgent, *, max_retries: int = DEFAULT_RECOVERY_MAX_RETRIES) -> None:
        self.orchestrator = orchestrator
        self.max_retries = max_retries
        self._retry_counts: dict[tuple[str, str], int] = {}

    def register(self, dispatcher: EventDispatcher) -> None:
        dispatcher.register(StepEvent.TOOL_FAILED.value, self.handle_tool_failed)

    def handle_tool_failed(self, event: ToolFailed) -> None:  # pragma: no cover - registration hook
        return None

    async def recover_failed_step(
        self,
        task: Task,
        plan: Plan,
        step: PlanStep,
        result: ToolResult | None,
        context: dict,
        observation: ToolResult | None,
        *,
        threaded_tools: bool = False,
        recovery_chain_id: str | None = None,
    ) -> StepExecutionOutcome:
        orchestrator = self.orchestrator
        chain_id = recovery_chain_id or step.id
        key = (task.id, chain_id)
        error = result.error if result else "Tool failed without a result."
        await orchestrator.dispatcher.dispatch(
            ToolFailed(
                task_id=task.id,
                source_agent=orchestrator.name,
                step_id=step.id,
                tool_name=step.tool_name,
                error=error,
                retry_count=self._retry_counts.get(key, 0),
            )
        )

        retry_count = self._retry_counts.get(key, 0)
        if retry_count >= self.max_retries:
            return await self.rollback_and_fail(task, plan, step, result, reason="retry_limit")
        self._retry_counts[key] = retry_count + 1

        recovery_observation = self._recovery_observation(step, result, observation)
        action = await orchestrator._consult_subagent(task, step, observation=recovery_observation)
        if not self._is_recovery_action(action):
            return await self.rollback_and_fail(task, plan, step, result, reason="no_alternative")

        recovery_step = self._create_recovery_step(step, action)
        plan.steps.append(recovery_step)
        orchestrator._persist_plan_update(plan, f"Added recovery step for failed step {step.id}.")
        orchestrator.bus.publish_text(
            task.id,
            orchestrator.name,
            f"Trying recovery step after {step.tool_name} failed.",
            message_type=MessageType.REVISION,
            step_id=recovery_step.id,
            structured_payload={
                "failed_step_id": step.id,
                "recovery_step": recovery_step.model_dump(),
                "retry": self._retry_counts[key],
            },
        )
        record(
            "task.recovery_step_created",
            orchestrator.name,
            {"failed_step": step.id, "recovery_step": recovery_step.id, "tool": recovery_step.tool_name},
            task_id=task.id,
        )

        outcome = await orchestrator._execute_step(
            task,
            plan,
            recovery_step,
            context,
            recovery_observation,
            threaded_tools=threaded_tools,
        )
        if outcome.kind in {"succeeded", "skipped"}:
            set_step_status(step, StepStatus.SKIPPED, actor="RecoveryHandler")
            return StepExecutionOutcome("recovered", outcome.result or result)
        if outcome.kind == "failed":
            return await self.recover_failed_step(
                task,
                plan,
                recovery_step,
                outcome.result,
                context,
                recovery_observation,
                threaded_tools=threaded_tools,
                recovery_chain_id=chain_id,
            )
        return outcome

    async def rollback_and_fail(
        self,
        task: Task,
        plan: Plan,
        step: PlanStep,
        result: ToolResult | None,
        *,
        reason: str,
    ) -> StepExecutionOutcome:
        orchestrator = self.orchestrator
        rollback = rollback_tools.execute_rollback(task.id)
        set_step_status(step, StepStatus.FAILED, actor="RecoveryHandler")
        orchestrator._set_status(
            task,
            TaskStatus.FAILED,
            final_summary=orchestrator._friendly_tool_error(result.error if result else "Tool failed."),
        )
        orchestrator._persist_plan_update(plan, "Plan failed after recovery was exhausted; rollback attempted.")
        orchestrator.bus.publish_text(
            task.id,
            "RollbackTool",
            "Recovery failed; attempted rollback for completed modifying steps.",
            message_type=MessageType.OBSERVATION,
            step_id=step.id,
            structured_payload={"reason": reason, "rollback": rollback},
        )
        record(
            "task.recovery_failed_rollback",
            orchestrator.name,
            {"step": step.id, "reason": reason, "rollback_count": rollback.get("count", 0)},
            task_id=task.id,
        )
        return StepExecutionOutcome("fatal_failed", result)

    def _create_recovery_step(self, failed_step: PlanStep, action: AgentAction) -> PlanStep:
        tool_name = action.tool_name or failed_step.tool_name
        args = dict(action.args or failed_step.args or {})
        return PlanStep(
            task_id=failed_step.task_id,
            order=failed_step.order + 1,
            agent_name=failed_step.agent_name,
            tool_name=tool_name,
            description=action.rationale or f"Recover failed step: {failed_step.description}",
            args=args,
            expected_observation=f"Recovery for {failed_step.id} completes successfully.",
            risk_level=failed_step.risk_level,
            requires_approval=failed_step.requires_approval or bool(action.tool_name and action.tool_name != failed_step.tool_name),
            depends_on=[],
            rollback_strategy=failed_step.rollback_strategy,
        )

    def _recovery_observation(
        self,
        step: PlanStep,
        result: ToolResult | None,
        previous_observation: ToolResult | None,
    ) -> ToolResult:
        if result is not None:
            return result
        if previous_observation is not None:
            return previous_observation
        return ToolResult(
            tool_call_id=f"{step.id}_recovery_observation",
            ok=False,
            error="Tool failed without a result.",
            observation=f"{step.tool_name} failed; propose a safe recovery step if possible.",
        )

    def _is_recovery_action(self, action: AgentAction | None) -> bool:
        return bool(action and action.kind == "propose_tool")
