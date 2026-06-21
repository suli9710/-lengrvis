from __future__ import annotations
import logging
import threading
from typing import TYPE_CHECKING

from app.core.audit import record
from app.core.schemas import AgentAction, MessageType, Plan, PlanStep, StepStatus, Task, TaskStatus, ToolResult
from app.orchestration.events import ToolFailed
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.step_phase import set_step_status
from app.policy.risk import RiskLevel
from app.tools import rollback_tools

if TYPE_CHECKING:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.orchestration.dispatcher import EventDispatcher


DEFAULT_RECOVERY_MAX_RETRIES = 3
logger = logging.getLogger(__name__)

# --- P1 fix: Bounded retry tracking ---
# Previously _retry_counts was a plain dict that grew indefinitely as new
# (task_id, chain_id) pairs accumulated across long-running sessions. This
# caused unbounded memory growth because completed task retry entries were
# never cleaned up.
#
# The dict is now guarded by a lock and capped at _MAX_RETRY_ENTRIES. When the
# cap is exceeded, the oldest entries are evicted (FIFO). Additionally, a
# cleanup method is provided for callers to explicitly remove entries for
# completed tasks.

_MAX_RETRY_ENTRIES = 5000


class RecoveryHandler:
    """Recover from tool failures without pushing fallback logic into the scheduler."""

    def __init__(self, orchestrator: OrchestratorAgent, *, max_retries: int = DEFAULT_RECOVERY_MAX_RETRIES) -> None:
        self.orchestrator = orchestrator
        self.max_retries = max_retries
        self._retry_counts: dict[tuple[str, str], int] = {}
        self._retry_lock = threading.Lock()

    def register(self, _dispatcher: EventDispatcher) -> None:
        """Compatibility no-op.

        ``tool.failed`` is still emitted from ``recover_failed_step`` for
        notification and audit, but recovery itself is called directly.
        """
        return None

    def _get_retry_count(self, key: tuple[str, str]) -> int:
        with self._retry_lock:
            return self._retry_counts.get(key, 0)

    def _increment_retry_count(self, key: tuple[str, str]) -> int:
        with self._retry_lock:
            # P1 fix: Evict oldest entries when the dict exceeds the cap to
            # prevent unbounded memory growth in long-running sessions.
            if len(self._retry_counts) >= _MAX_RETRY_ENTRIES:
                # Evict ~10% of oldest entries (dict preserves insertion order)
                evict_count = max(1, _MAX_RETRY_ENTRIES // 10)
                for _ in range(evict_count):
                    if self._retry_counts:
                        oldest_key = next(iter(self._retry_counts))
                        del self._retry_counts[oldest_key]
                logger.info(
                    "recovery_handler: evicted %d stale retry entries (cap=%d)",
                    evict_count,
                    _MAX_RETRY_ENTRIES,
                )
            self._retry_counts[key] = self._retry_counts.get(key, 0) + 1
            return self._retry_counts[key]

    def cleanup_task(self, task_id: str) -> None:
        """Remove all retry tracking entries for a completed or cancelled task.

        Call this when a task reaches a terminal state to prevent the
        retry_counts dict from growing without bound across sessions.
        """
        with self._retry_lock:
            keys_to_remove = [k for k in self._retry_counts if k[0] == task_id]
            for k in keys_to_remove:
                del self._retry_counts[k]
            if keys_to_remove:
                logger.debug(
                    "recovery_handler: cleaned up %d retry entries for task %s",
                    len(keys_to_remove),
                    task_id,
                )

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
                retry_count=self._get_retry_count(key),
            )
        )

        retry_count = self._get_retry_count(key)
        if retry_count >= self.max_retries:
            return await self.rollback_and_fail(task, plan, step, result, reason="retry_limit")
        self._increment_retry_count(key)

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
                "retry": self._get_retry_count(key),
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
        risk = self._risk_for_tool(tool_name, failed_step.risk_level)
        return PlanStep(
            task_id=failed_step.task_id,
            order=failed_step.order + 1,
            agent_name=failed_step.agent_name,
            tool_name=tool_name,
            description=action.rationale or f"Recover failed step: {failed_step.description}",
            args=args,
            expected_observation=f"Recovery for {failed_step.id} completes successfully.",
            risk_level=risk,
            requires_approval=failed_step.requires_approval or self._risk_requires_approval(risk),
            depends_on=[],
            rollback_strategy=failed_step.rollback_strategy,
        )

    def _risk_for_tool(self, tool_name: str, fallback: RiskLevel) -> RiskLevel:
        try:
            return self.orchestrator.registry.get(tool_name).risk_level
        except Exception:
            return fallback

    def _risk_requires_approval(self, risk: RiskLevel) -> bool:
        return risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}

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
