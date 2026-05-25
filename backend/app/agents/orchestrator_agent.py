from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.app_agent import AppAgent
from app.agents.base import AgentContext, BaseAgent
from app.agents.browser_agent import BrowserAgent
from app.agents.computer_agent import ComputerAgent
from app.agents.document_agent import DocumentAgent
from app.agents.file_agent import FileAgent
from app.agents.memory_agent import MemoryAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.safety_review_agent import SafetyReviewAgent
from app.agents.search_agent import SearchAgent
from app.core import db
from app.core.audit import record
from app.core.schemas import (
    AgentAction,
    AgentMessage,
    Approval,
    MessageType,
    OpenAIMessageRole,
    Plan,
    PlanStep,
    StepStatus,
    Task,
    TaskStatus,
    ToolCall,
    ToolResult,
    now_iso,
)
from app.llm.registry import get_effective_settings, get_provider
from app.orchestration.agent_bus import AgentBus
from app.orchestration.state_machine import safe_transition
from app.policy.policy_engine import BROWSER_WRITE_TOOLS
from app.policy.risk import SafetyVerdict
from app.services.approval_event_service import publish_approval_created
from app.services.task_recording_service import capture_step_screenshot, recording_enabled
from app.tools.registry import register_all_tools


@dataclass(slots=True)
class StepExecutionOutcome:
    kind: str
    result: ToolResult | None = None


class OrchestratorAgent:
    name = "OrchestratorAgent"

    def __init__(self) -> None:
        self.bus = AgentBus()
        self.planner = PlannerAgent(self.bus)
        self.safety = SafetyReviewAgent(self.bus)
        self.memory = MemoryAgent(self.bus)
        self.subagents: dict[str, BaseAgent] = {
            "FileAgent": FileAgent(self.bus),
            "DocumentAgent": DocumentAgent(self.bus),
            "ComputerAgent": ComputerAgent(self.bus),
            "AppAgent": AppAgent(self.bus),
            "BrowserAgent": BrowserAgent(self.bus),
            "SearchAgent": SearchAgent(self.bus),
        }
        self.registry = register_all_tools(settings=get_effective_settings())
        self._supervised: dict[str, set[str]] = {}
        self._supervision_cursor: dict[str, str] = {}
        self._path_locks: dict[str, asyncio.Lock] = {}

    def _set_status(self, task: Task, status: TaskStatus, *, final_summary: str | None = None) -> Task:
        if final_summary is not None:
            task.final_summary = final_summary
        return safe_transition(task, status, actor=self.name)

    def create_task_shell(self, goal: str, mode: str) -> Task:
        task = Task(user_goal=goal, mode=mode, status=TaskStatus.PLANNING)
        db.upsert_model("tasks", task)
        record("task.created", self.name, {"goal": goal, "mode": mode}, task_id=task.id)
        self.bus.publish_text(
            task.id,
            "User",
            goal,
            role=OpenAIMessageRole.USER,
            message_type=MessageType.PROPOSAL,
            to_agent=self.name,
        )
        return task

    async def handle_user_goal(self, goal: str, mode: str) -> Task:
        task = self.create_task_shell(goal, mode)
        return await self.run_task(task)

    async def run_task(self, task: Task) -> Task:
        goal = task.user_goal
        mode = task.mode
        if not self._supervise_new_agent_messages(task.id, "user_goal"):
            return self._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task during initial runtime supervision.",
            )

        goal_review = self.safety.review_goal(task.id, goal)
        if goal_review.verdict == SafetyVerdict.DENY:
            return self._set_status(task, TaskStatus.DENIED, final_summary=goal_review.safe_alternative)

        memory_context = await self._recall_memory(goal)
        plan = await self.planner.create_plan(
            task.id,
            goal,
            mode,
            [tool.name for tool in self.registry.list()],
            memory_context=memory_context,
        )
        db.upsert_model("plans", plan)
        if not self._supervise_new_agent_messages(task.id, "planner_output"):
            return self._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task after PlannerAgent output.",
            )

        self._set_status(task, TaskStatus.AGENT_CONSULTATION)
        for agent in self.subagents.values():
            agent.consult(plan)
            if not self._supervise_new_agent_messages(task.id, f"{agent.name}_consultation"):
                return self._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary=f"SafetyReviewAgent stopped the task after {agent.name} consultation.",
                )

        plan_review = self.safety.review_plan(plan)
        self._set_status(task, TaskStatus.REVIEWING_PLAN)

        if plan_review.verdict == SafetyVerdict.DENY:
            return self._set_status(task, TaskStatus.DENIED, final_summary=plan_review.safe_alternative)

        await self._process_steps(task, plan)
        if task.status not in {TaskStatus.DENIED, TaskStatus.FAILED}:
            final_review = self.safety.final_review(plan, task.status, task.final_summary)
            if final_review.verdict == SafetyVerdict.DENY:
                self._set_status(task, TaskStatus.DENIED, final_summary=final_review.safe_alternative)
        if task.status == TaskStatus.COMPLETED:
            await self._consolidate_memory(task, plan)
        return task

    async def _process_steps(self, task: Task, plan: Plan) -> None:
        try:
            by_id, _dependents = self._build_step_graph(plan)
        except ValueError as exc:
            for step in plan.steps:
                if step.status == StepStatus.PENDING:
                    step.status = StepStatus.FAILED
            self._set_status(task, TaskStatus.FAILED, final_summary=str(exc))
            record("task.step_graph_invalid", self.name, {"error": str(exc)}, task_id=task.id)
            return

        context = self._tool_context()
        pending = {
            step.id
            for step in plan.steps
            if step.status
            not in {
                StepStatus.SUCCEEDED,
                StepStatus.SKIPPED,
                StepStatus.FAILED,
                StepStatus.DENIED,
                StepStatus.WAITING_USER_APPROVAL,
            }
        }
        running: dict[asyncio.Task, PlanStep] = {}
        observations: dict[str, ToolResult] = {}
        any_waiting = False
        revision_requested = False
        stop_requested = False

        while pending or running:
            if not stop_requested:
                ready = self._ready_steps(pending, by_id)
                threaded_tools = len(ready) > 1
                if len(ready) == 1 and not running:
                    step = ready[0]
                    pending.remove(step.id)
                    observation = self._dependency_observation(step, observations)
                    outcome = await self._execute_step(task, plan, step, context, observation, threaded_tools=False)
                    if outcome.result is not None:
                        observations[step.id] = outcome.result
                    if outcome.kind == "waiting_user_approval":
                        any_waiting = True
                        stop_requested = True
                    elif outcome.kind == "revision_requested":
                        revision_requested = True
                        stop_requested = True
                    elif outcome.kind in {"fatal_denied", "fatal_failed"}:
                        stop_requested = True
                    if stop_requested:
                        break
                    continue
                for step in ready:
                    pending.remove(step.id)
                    observation = self._dependency_observation(step, observations)
                    work = asyncio.create_task(
                        self._execute_step(task, plan, step, context, observation, threaded_tools=threaded_tools),
                        name=f"step-{step.id}",
                    )
                    running[work] = step

            if not running:
                self._mark_blocked_steps(pending, by_id)
                break

            done, _ = await asyncio.wait(running.keys(), return_when=asyncio.FIRST_COMPLETED)
            outcomes = await asyncio.gather(*done, return_exceptions=True)
            for work, outcome in zip(done, outcomes):
                step = running.pop(work)
                if isinstance(outcome, Exception):
                    step.status = StepStatus.FAILED
                    self._set_status(task, TaskStatus.FAILED, final_summary=self._friendly_tool_error(str(outcome)))
                    record("task.step_failed_unhandled", self.name, {"step": step.id, "error": str(outcome)}, task_id=task.id)
                    stop_requested = True
                    continue
                if outcome.result is not None:
                    observations[step.id] = outcome.result
                if outcome.kind == "waiting_user_approval":
                    any_waiting = True
                    stop_requested = True
                elif outcome.kind == "revision_requested":
                    revision_requested = True
                    stop_requested = True
                elif outcome.kind in {"fatal_denied", "fatal_failed"}:
                    stop_requested = True

            if stop_requested and running:
                remaining = list(running.keys())
                outcomes = await asyncio.gather(*remaining, return_exceptions=True)
                for work, outcome in zip(remaining, outcomes):
                    step = running.pop(work)
                    if isinstance(outcome, Exception):
                        step.status = StepStatus.FAILED
                        record("task.step_failed_unhandled", self.name, {"step": step.id, "error": str(outcome)}, task_id=task.id)
                        continue
                    if outcome.result is not None:
                        observations[step.id] = outcome.result
                    if outcome.kind == "waiting_user_approval":
                        any_waiting = True
                    elif outcome.kind == "revision_requested":
                        revision_requested = True
                break

            if not running and pending and not self._ready_steps(pending, by_id):
                self._mark_blocked_steps(pending, by_id)
                break

        if task.status in {TaskStatus.DENIED, TaskStatus.FAILED}:
            record("task.finished_or_waiting", self.name, {"status": task.status}, task_id=task.id)
            return
        if revision_requested:
            target = TaskStatus.PAUSED
            summary = "A subagent requested plan revision; automatic replanning was not repeated for this step."
        elif any_waiting:
            target = TaskStatus.WAITING_USER_APPROVAL
            summary = "Plan generated and waiting for approval on modifying steps."
        elif any(step.status == StepStatus.FAILED for step in plan.steps):
            target = TaskStatus.FAILED
            summary = "Task failed while processing one or more steps."
        else:
            target = TaskStatus.COMPLETED
            summary = "Task completed with read-only/open-only MVP tools."
        self._set_status(task, target, final_summary=summary)
        record("task.finished_or_waiting", self.name, {"status": task.status}, task_id=task.id)

    async def _execute_step(
        self,
        task: Task,
        plan: Plan,
        step: PlanStep,
        context: dict[str, Any],
        observation: ToolResult | None,
        *,
        threaded_tools: bool = False,
    ) -> StepExecutionOutcome:
        step.task_id = step.task_id or task.id
        self._set_status(task, TaskStatus.EXECUTING_STEP)
        try:
            tool = self.registry.get(step.tool_name)
        except KeyError as exc:
            step.status = StepStatus.FAILED
            self._set_status(task, TaskStatus.FAILED, final_summary=self._friendly_tool_error(str(exc)))
            return StepExecutionOutcome("fatal_failed")

        risk = tool.risk_level
        action = await self._consult_subagent(task, step, observation=observation)
        if action and action.kind == "done":
            step.status = StepStatus.SKIPPED
            result = ToolResult(
                tool_call_id=f"{step.id}_subagent_done",
                ok=True,
                observation=action.rationale or f"{step.tool_name} already complete.",
            )
            self.bus.publish_text(
                task.id,
                self.name,
                f"Skipped step after {step.agent_name} marked it done: {step.description}",
                message_type=MessageType.OBSERVATION,
                step_id=step.id,
                structured_payload={"subagent_action": action.model_dump(), "skipped": True},
            )
            self._supervise_new_agent_messages(task.id, "subagent_done")
            return StepExecutionOutcome("skipped", result)
        if action and action.kind == "request_revision":
            self._handle_subagent_revision_request(task, step, action)
            result = ToolResult(
                tool_call_id=f"{step.id}_revision_request",
                ok=True,
                observation=action.follow_up_question or action.rationale or "Subagent requested plan revision.",
            )
            if not self._supervise_new_agent_messages(task.id, "subagent_revision_request"):
                step.status = StepStatus.DENIED
                self._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary="SafetyReviewAgent stopped the task after a subagent revision request.",
                )
                return StepExecutionOutcome("fatal_denied", result)
            return StepExecutionOutcome("revision_requested", result)
        if action and action.kind == "propose_tool":
            try:
                tool = self._apply_subagent_tool_proposal(task, step, action)
            except KeyError as exc:
                step.status = StepStatus.FAILED
                self._set_status(task, TaskStatus.FAILED, final_summary=self._friendly_tool_error(str(exc)))
                self.bus.publish_text(
                    task.id,
                    self.name,
                    f"Subagent proposed an unavailable tool: {action.tool_name}",
                    message_type=MessageType.REVISION,
                    step_id=step.id,
                    structured_payload={"subagent_action": action.model_dump(), "error": str(exc)},
                )
                self._supervise_new_agent_messages(task.id, "subagent_invalid_tool")
                return StepExecutionOutcome("fatal_failed")
            risk = tool.risk_level
            if not self._supervise_new_agent_messages(task.id, "subagent_proposal_applied"):
                step.status = StepStatus.DENIED
                self._set_status(
                    task,
                    TaskStatus.DENIED,
                    final_summary="SafetyReviewAgent stopped the task after applying a subagent proposal.",
                )
                return StepExecutionOutcome("fatal_denied")
            self._persist_plan_update(plan, "Plan step updated from subagent tool proposal.")
        self._set_status(task, TaskStatus.REVIEWING_TOOL_CALL)
        if step.tool_name in BROWSER_WRITE_TOOLS:
            browser_review = self.safety.review_browser_write(task.id, step.id, step.tool_name, step.args)
            if browser_review and browser_review.verdict == SafetyVerdict.DENY:
                step.status = StepStatus.DENIED
                self.bus.publish_text(
                    task.id,
                    self.name,
                    f"Denied browser write {step.tool_name}: {'; '.join(browser_review.reasons)}",
                    step_id=step.id,
                )
                self._supervise_new_agent_messages(task.id, "browser_write_denied")
                return StepExecutionOutcome("step_denied")
        review = self.safety.review_tool_call(task.id, step.id, step.tool_name, step.args, risk)
        if review.verdict == SafetyVerdict.DENY:
            step.status = StepStatus.DENIED
            self.bus.publish_text(task.id, self.name, f"Denied step: {step.description}", step_id=step.id)
            self._supervise_new_agent_messages(task.id, "tool_call_denied")
            return StepExecutionOutcome("step_denied")
        if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
            before_frame = await self._capture_step_frame(task, step, "before_dry_run")
            try:
                preview = await self._execute_tool_with_locks(
                    tool,
                    step,
                    {**step.args, "dry_run": True},
                    context,
                    threaded=threaded_tools,
                )
            except Exception as exc:  # noqa: BLE001
                preview = {"error": str(exc)}
            finally:
                after_frame = await self._capture_step_frame(task, step, "after_dry_run")
                self._publish_step_recording(
                    task,
                    step,
                    [before_frame, after_frame],
                    tool_name=step.tool_name,
                    agent=step.agent_name,
                )
            preview_result = ToolResult(
                tool_call_id=f"{step.id}_dry_run",
                ok=not bool(preview.get("error")),
                output=preview,
                error=str(preview.get("error", "")),
                observation=f"{step.tool_name} dry-run preview generated.",
            )
            if not preview_result.ok:
                step.status = StepStatus.FAILED
                self._set_status(
                    task,
                    TaskStatus.FAILED,
                    final_summary=self._friendly_tool_error(preview_result.error),
                )
                self.bus.publish_text(
                    task.id,
                    step.agent_name,
                    task.final_summary,
                    role=OpenAIMessageRole.TOOL,
                    message_type=MessageType.OBSERVATION,
                    step_id=step.id,
                    structured_payload=preview_result.model_dump(),
                )
                return StepExecutionOutcome("fatal_failed", preview_result)
            post_preview_review = self.safety.review_tool_result(
                task.id,
                step.id,
                step.tool_name,
                preview_result,
                risk,
            )
            if post_preview_review.verdict == SafetyVerdict.DENY:
                step.status = StepStatus.DENIED
                self._set_status(task, TaskStatus.DENIED, final_summary=post_preview_review.safe_alternative)
                return StepExecutionOutcome("fatal_denied", preview_result)
            approval = Approval(
                task_id=task.id,
                step_id=step.id,
                message=review.user_confirmation_message or step.description,
                diff_preview=preview,
            )
            db.upsert_model("approvals", approval)
            publish_approval_created(approval)
            step.status = StepStatus.WAITING_USER_APPROVAL
            self.bus.publish_text(
                task.id,
                "HumanGateAgent",
                "Waiting for user approval before executing modifying operation.",
                message_type=MessageType.REVIEW,
                step_id=step.id,
            )
            self._supervise_new_agent_messages(task.id, "approval_gate")
            return StepExecutionOutcome("waiting_user_approval", preview_result)

        call = ToolCall(task_id=task.id, step_id=step.id, tool_name=step.tool_name, args=step.args, risk_level=risk, dry_run=False)
        db.upsert_model("tool_calls", call)
        self.bus.publish_text(
            task.id,
            self.name,
            f"Calling tool {step.tool_name}.",
            message_type=MessageType.PROPOSAL,
            step_id=step.id,
            tool_calls=[
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": step.tool_name,
                        "arguments": step.args,
                    },
                }
            ],
            structured_payload=call.model_dump(),
        )
        if not self._supervise_new_agent_messages(task.id, "tool_call_proposed"):
            step.status = StepStatus.DENIED
            self._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task before executing a tool call.",
            )
            return StepExecutionOutcome("fatal_denied")
        before_frame = await self._capture_step_frame(task, step, "before")
        try:
            step.status = StepStatus.RUNNING
            self._set_status(task, TaskStatus.EXECUTING_TOOL)
            output = await self._execute_tool_with_locks(tool, step, step.args, context, threaded=threaded_tools)
            result = ToolResult(
                tool_call_id=call.id,
                ok=not bool(output.get("error")),
                output=output,
                error=str(output.get("error", "")),
                changed_paths=list(output.get("changed_paths", [])),
                rollback_info=dict(output.get("rollback_info", {})),
                observation=step.expected_observation or f"{step.tool_name} completed.",
            )
        except Exception as exc:
            result = ToolResult(tool_call_id=call.id, ok=False, error=str(exc), observation=f"{step.tool_name} failed.")
        finally:
            after_frame = await self._capture_step_frame(task, step, "after")
            self._publish_step_recording(task, step, [before_frame, after_frame], tool_name=step.tool_name, agent=step.agent_name)
        db.upsert_model("tool_results", result)
        post_tool_review = self.safety.review_tool_result(task.id, step.id, step.tool_name, result, risk)
        if post_tool_review.verdict == SafetyVerdict.DENY:
            step.status = StepStatus.DENIED
            self._set_status(task, TaskStatus.DENIED, final_summary=post_tool_review.safe_alternative)
            return StepExecutionOutcome("fatal_denied", result)
        self.bus.publish_text(
            task.id,
            step.agent_name,
            result.observation if result.ok else result.error,
            role=OpenAIMessageRole.TOOL,
            message_type=MessageType.OBSERVATION,
            step_id=step.id,
            tool_call_id=call.id,
            structured_payload=result.model_dump(),
        )
        if not self._supervise_new_agent_messages(task.id, "tool_observation"):
            step.status = StepStatus.DENIED
            self._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="SafetyReviewAgent stopped the task after observing tool output.",
            )
            return StepExecutionOutcome("fatal_denied", result)
        step.status = StepStatus.SUCCEEDED if result.ok else StepStatus.FAILED
        await self._reflect_on_step(task, step, result)
        return StepExecutionOutcome("succeeded" if result.ok else "failed", result)

    async def execute_approved_step(self, approval: Approval) -> Task:
        task_data = db.fetch_one("tasks", approval.task_id)
        if not task_data:
            raise KeyError(f"Task not found: {approval.task_id}")
        task = Task.model_validate(task_data)

        plan = self._latest_plan_for_task(task.id)
        step = next((item for item in plan.steps if item.id == approval.step_id), None)
        if step is None:
            raise KeyError(f"Step not found for approval: {approval.step_id}")
        if step.status == StepStatus.SUCCEEDED:
            return task

        tool = self.registry.get(step.tool_name)
        action = None if approval.approval_type == "remote_input" else await self._consult_subagent(task, step, observation=None)
        if action and action.kind == "done":
            step.status = StepStatus.SKIPPED
            self._persist_plan_update(plan, "Approved step skipped after subagent marked it done.")
            self._set_status(task, TaskStatus.COMPLETED, final_summary="Approved step was already complete.")
            return task
        if action and action.kind == "request_revision":
            self._handle_subagent_revision_request(task, step, action)
            step.status = StepStatus.SKIPPED
            self._persist_plan_update(plan, "Approved step paused after subagent requested plan revision.")
            self._set_status(
                task,
                TaskStatus.PAUSED,
                final_summary="A subagent requested plan revision before the approved step could execute.",
            )
            return task
        if action and action.kind == "propose_tool":
            proposed_tool_name = action.tool_name or step.tool_name
            merged_args = {**dict(step.args or {}), **dict(action.args or {})}
            if proposed_tool_name != step.tool_name or merged_args != step.args:
                self._handle_subagent_revision_request(task, step, action)
                step.status = StepStatus.SKIPPED
                self._persist_plan_update(plan, "Approved step paused because subagent proposed a different tool call.")
                self._set_status(
                    task,
                    TaskStatus.PAUSED,
                    final_summary="A subagent proposed a different tool call after approval; a fresh review is required.",
                )
                return task
        args = {**step.args, "dry_run": False, "approved": True, "approval_id": approval.id}
        call = ToolCall(
            task_id=task.id,
            step_id=step.id,
            tool_name=step.tool_name,
            args=args,
            risk_level=tool.risk_level,
            dry_run=False,
        )
        db.upsert_model("tool_calls", call)

        step.status = StepStatus.RUNNING
        self._set_status(task, TaskStatus.EXECUTING_TOOL)
        self._persist_plan_update(plan, "Plan status updated after user approval.")

        self.bus.publish_text(
            task.id,
            self.name,
            f"Calling approved tool {step.tool_name}.",
            message_type=MessageType.PROPOSAL,
            step_id=step.id,
            tool_calls=[
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": step.tool_name,
                        "arguments": args,
                    },
                }
            ],
            structured_payload=call.model_dump(),
            metadata={"approval_id": approval.id, "approved_by_user": True},
        )
        if not self._supervise_new_agent_messages(task.id, "approved_tool_call_proposed"):
            step.status = StepStatus.DENIED
            self._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="安全审核 Agent 在执行已批准工具前拦截了任务。",
            )
            self._persist_plan_update(plan, "Plan denied before approved tool execution.")
            return task

        before_frame = await self._capture_step_frame(task, step, "before_approved")
        try:
            output = await self._execute_tool_with_locks(tool, step, args, self._tool_context())
            result = ToolResult(
                tool_call_id=call.id,
                ok=not bool(output.get("error")),
                output=output,
                error=str(output.get("error", "")),
                changed_paths=list(output.get("changed_paths", [])),
                rollback_info=dict(output.get("rollback_info", {})),
                observation=step.expected_observation or f"{step.tool_name} completed.",
            )
        except Exception as exc:
            result = ToolResult(
                tool_call_id=call.id,
                ok=False,
                error=str(exc),
                observation=f"{step.tool_name} failed.",
            )
        finally:
            after_frame = await self._capture_step_frame(task, step, "after_approved")
            self._publish_step_recording(
                task,
                step,
                [before_frame, after_frame],
                tool_name=step.tool_name,
                agent=step.agent_name,
                metadata={"approval_id": approval.id, "approved_by_user": True},
            )

        db.upsert_model("tool_results", result)
        post_tool_review = self.safety.review_tool_result(task.id, step.id, step.tool_name, result, tool.risk_level)
        if post_tool_review.verdict == SafetyVerdict.DENY:
            step.status = StepStatus.DENIED
            self._set_status(task, TaskStatus.DENIED, final_summary=post_tool_review.safe_alternative)
            self._persist_plan_update(plan, "Plan denied after approved tool execution.")
            return task

        self.bus.publish_text(
            task.id,
            step.agent_name,
            result.observation if result.ok else self._friendly_tool_error(result.error),
            role=OpenAIMessageRole.TOOL,
            message_type=MessageType.OBSERVATION,
            step_id=step.id,
            tool_call_id=call.id,
            structured_payload=result.model_dump(),
        )
        if not self._supervise_new_agent_messages(task.id, "approved_tool_observation"):
            step.status = StepStatus.DENIED
            self._set_status(
                task,
                TaskStatus.DENIED,
                final_summary="安全审核 Agent 在观察已批准工具结果后拦截了任务。",
            )
            self._persist_plan_update(plan, "Plan denied after approved tool observation.")
            return task

        step.status = StepStatus.SUCCEEDED if result.ok else StepStatus.FAILED
        if result.ok:
            pending_approvals = db.fetch_many("approvals", "task_id = ? AND status = ?", (task.id, "pending"), limit=100)
            target_status = TaskStatus.WAITING_USER_APPROVAL if pending_approvals else TaskStatus.COMPLETED
            summary = (
                "已按你的审批执行，目标已移入回收站。"
                if step.tool_name == "file.trash"
                else "已按你的审批执行修改操作。"
            )
            self._set_status(task, target_status, final_summary=summary)
        else:
            self._set_status(task, TaskStatus.FAILED, final_summary=self._friendly_tool_error(result.error))
        self._persist_plan_update(plan, "Plan status updated after approved tool execution.")
        record("task.approved_step_executed", self.name, {"approval_id": approval.id, "ok": result.ok}, task_id=task.id)
        return task

    def _latest_plan_for_task(self, task_id: str) -> Plan:
        plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
        if not plans:
            raise KeyError(f"Plan not found for task: {task_id}")
        return Plan.model_validate(plans[0])

    def _tool_context(self) -> dict:
        settings = get_effective_settings()
        return {"allowed_directories": settings.allowed_directories, "settings": settings}

    async def _capture_step_frame(self, task: Task, step: PlanStep, phase: str) -> dict[str, Any]:
        if not recording_enabled():
            return capture_step_screenshot(task.id, step.id, phase)
        return await asyncio.to_thread(capture_step_screenshot, task.id, step.id, phase)

    def _publish_step_recording(
        self,
        task: Task,
        step: PlanStep,
        frames: list[dict[str, Any]],
        *,
        tool_name: str,
        agent: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if frames and not any(frame.get("enabled", True) for frame in frames):
            return
        payload = {
            "kind": "step_screenshot",
            "task_id": task.id,
            "step_id": step.id,
            "step_order": step.order,
            "step_description": step.description,
            "tool_name": tool_name,
            "agent": agent,
            "frames": frames,
            "ok": all(frame.get("ok") for frame in frames),
            **(metadata or {}),
        }
        self.bus.publish_text(
            task.id,
            self.name,
            f"Recorded before/after screenshots for {tool_name}.",
            message_type=MessageType.OBSERVATION,
            step_id=step.id,
            structured_payload=payload,
            metadata={"recording": True, **(metadata or {})},
        )
        record(
            "task.step_recorded",
            self.name,
            {
                "step_id": step.id,
                "tool_name": tool_name,
                "frame_count": len(frames),
                "ok": payload["ok"],
            },
            task_id=task.id,
        )

    def _build_step_graph(self, plan: Plan) -> tuple[dict[str, PlanStep], dict[str, set[str]]]:
        by_id: dict[str, PlanStep] = {}
        dependents: dict[str, set[str]] = {}
        for idx, step in enumerate(plan.steps, start=1):
            if not step.id:
                step.id = f"step_{idx}"
            if step.id in by_id:
                raise ValueError(f"Duplicate plan step id: {step.id}")
            by_id[step.id] = step
            dependents.setdefault(step.id, set())

        for step in plan.steps:
            normalized: list[str] = []
            for dependency in step.depends_on:
                dependency_id = str(dependency).strip()
                if not dependency_id:
                    continue
                if dependency_id == step.id:
                    raise ValueError(f"Plan step {step.id} cannot depend on itself.")
                if dependency_id not in by_id:
                    raise ValueError(f"Plan step {step.id} depends on unknown step id: {dependency_id}")
                if dependency_id not in normalized:
                    normalized.append(dependency_id)
                dependents.setdefault(dependency_id, set()).add(step.id)
            step.depends_on = normalized

        if self._has_step_cycle(by_id):
            raise ValueError("Plan step dependency graph contains a cycle.")
        return by_id, dependents

    def _has_step_cycle(self, by_id: dict[str, PlanStep]) -> bool:
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(step_id: str) -> bool:
            if step_id in permanent:
                return False
            if step_id in temporary:
                return True
            temporary.add(step_id)
            for dependency in by_id[step_id].depends_on:
                if visit(dependency):
                    return True
            temporary.remove(step_id)
            permanent.add(step_id)
            return False

        return any(visit(step_id) for step_id in by_id)

    def _ready_steps(self, pending: set[str], by_id: dict[str, PlanStep]) -> list[PlanStep]:
        ready = [
            by_id[step_id]
            for step_id in pending
            if all(self._dependency_finished(by_id[dependency]) for dependency in by_id[step_id].depends_on)
        ]
        return sorted(ready, key=lambda step: (step.order, step.id))

    def _dependency_finished(self, step: PlanStep) -> bool:
        return step.status in {
            StepStatus.SUCCEEDED,
            StepStatus.SKIPPED,
        }

    def _dependency_observation(self, step: PlanStep, observations: dict[str, ToolResult]) -> ToolResult | None:
        for dependency in reversed(step.depends_on):
            if dependency in observations:
                return observations[dependency]
        return None

    def _mark_blocked_steps(self, pending: set[str], by_id: dict[str, PlanStep]) -> None:
        for step_id in list(pending):
            step = by_id[step_id]
            blocked = [
                dependency
                for dependency in step.depends_on
                if by_id[dependency].status in {StepStatus.FAILED, StepStatus.DENIED, StepStatus.WAITING_USER_APPROVAL}
            ]
            if blocked:
                step.status = StepStatus.SKIPPED
                pending.remove(step_id)
                self.bus.publish_text(
                    step.task_id,
                    self.name,
                    f"Skipped step because dependency did not complete: {', '.join(blocked)}",
                    message_type=MessageType.OBSERVATION,
                    step_id=step.id,
                    structured_payload={"blocked_by": blocked},
                )

    async def _execute_tool_with_locks(
        self,
        tool,
        step: PlanStep,
        args: dict[str, Any],
        context: dict[str, Any],
        *,
        threaded: bool = False,
    ) -> dict[str, Any]:
        lock_keys = self._write_lock_keys(tool, args)
        if not lock_keys:
            if threaded:
                return await asyncio.to_thread(tool.execute, args, context)
            return tool.execute(args, context)
        locks = [self._path_locks.setdefault(key, asyncio.Lock()) for key in lock_keys]
        return await self._execute_tool_under_locks(tool, args, context, locks, threaded=threaded)

    async def _execute_tool_under_locks(
        self,
        tool,
        args: dict[str, Any],
        context: dict[str, Any],
        locks: list[asyncio.Lock],
        *,
        threaded: bool = False,
    ) -> dict[str, Any]:
        if not locks:
            if threaded:
                return await asyncio.to_thread(tool.execute, args, context)
            return tool.execute(args, context)
        async with locks[0]:
            return await self._execute_tool_under_locks(tool, args, context, locks[1:], threaded=threaded)

    def _write_lock_keys(self, tool, args: dict[str, Any]) -> list[str]:
        if not self._is_write_tool(tool):
            return []
        if args.get("dry_run") is True:
            return []

        keys: set[str] = set()
        for value in self._candidate_write_paths(args):
            path = self._normalize_lock_path(value)
            if not path:
                continue
            keys.add(path)
            parent = str(Path(path).parent)
            if parent and parent != path:
                keys.add(parent)
        return sorted(keys)

    def _is_write_tool(self, tool) -> bool:
        risk = getattr(tool, "risk_level", None)
        risk_value = getattr(risk, "value", str(risk or ""))
        if risk and risk_value.startswith(("R2", "R3")):
            return True
        if getattr(tool, "supports_dry_run", False):
            return True
        name = getattr(tool, "name", "")
        return name in BROWSER_WRITE_TOOLS or any(
            token in name
            for token in (".copy", ".move", ".rename", ".trash", ".write", ".create", ".delete", ".uninstall")
        )

    def _candidate_write_paths(self, args: dict[str, Any]) -> list[Any]:
        result: list[Any] = []
        for key in (
            "path",
            "source",
            "destination",
            "target",
            "target_path",
            "target_folder",
            "folder",
            "directory",
            "output_path",
        ):
            value = args.get(key)
            if value:
                result.append(value)
        return result

    def _normalize_lock_path(self, value: Any) -> str:
        if not isinstance(value, (str, Path)):
            return ""
        text = str(value).strip()
        if not text:
            return ""
        try:
            return str(Path(text).expanduser().resolve(strict=False)).casefold()
        except OSError:
            return text.casefold()

    def _persist_plan_update(self, plan: Plan, content: str) -> None:
        db.upsert_model("plans", plan)
        self.bus.publish_text(
            plan.task_id,
            "PlannerAgent",
            content,
            message_type=MessageType.REVISION,
            structured_payload=plan.model_dump(),
        )

    def _apply_subagent_tool_proposal(self, task: Task, step: PlanStep, action: AgentAction):
        proposed_tool_name = action.tool_name or step.tool_name
        if proposed_tool_name == step.tool_name:
            proposed_args = {**dict(step.args or {}), **dict(action.args or {})}
        else:
            proposed_args = dict(action.args or {})
        tool = self.registry.get(proposed_tool_name)
        original = {"tool_name": step.tool_name, "args": dict(step.args or {}), "agent_name": step.agent_name}
        proposed_args = self._sanitize_subagent_args(tool, original["args"], proposed_args)
        changed = proposed_tool_name != step.tool_name or proposed_args != step.args
        step.tool_name = proposed_tool_name
        step.args = proposed_args
        step.agent_name = getattr(tool, "agent_owner", "") or step.agent_name
        step.risk_level = tool.risk_level
        step.requires_approval = (
            bool(step.requires_approval)
            or tool.risk_level.value.startswith(("R2", "R3"))
            or original["tool_name"] != proposed_tool_name
        )
        if changed:
            self.bus.publish_text(
                task.id,
                self.name,
                f"Using {step.agent_name} proposal for {step.tool_name}.",
                message_type=MessageType.REVISION,
                step_id=step.id,
                structured_payload={
                    "subagent_action": action.model_dump(),
                    "original_step": original,
                    "final_tool": step.tool_name,
                    "final_args": step.args,
                },
            )
            record(
                "subagent.proposal_applied",
                self.name,
                {
                    "step": step.id,
                    "original_tool": original["tool_name"],
                    "final_tool": step.tool_name,
                    "agent": step.agent_name,
                },
                task_id=task.id,
            )
        return tool

    def _sanitize_subagent_args(
        self,
        tool,
        original_args: dict,
        proposed_args: dict,
    ) -> dict:
        if not getattr(tool, "input_schema", None):
            return proposed_args
        properties = set((tool.input_schema.get("properties") or {}).keys())
        required = set(tool.input_schema.get("required") or [])
        if not properties and not required:
            return proposed_args
        allowed = properties | required
        merged = {key: value for key, value in proposed_args.items() if key in allowed}
        for key in required:
            if key in original_args and key not in merged:
                merged[key] = original_args[key]
        return merged

    def _handle_subagent_revision_request(self, task: Task, step: PlanStep, action: AgentAction) -> None:
        step.status = StepStatus.SKIPPED
        question = action.follow_up_question or action.rationale or "Subagent requested a plan revision."
        try:
            tool = self.registry.get(step.tool_name) if step.tool_name else None
            from_agent = getattr(tool, "agent_owner", "") or step.agent_name
        except Exception:
            from_agent = step.agent_name
        self.bus.publish_text(
            task.id,
            from_agent,
            question,
            message_type=MessageType.REVISION,
            to_agent="PlannerAgent",
            step_id=step.id,
            structured_payload={"subagent_action": action.model_dump(), "revision_requested": True},
        )
        self.bus.publish_text(
            task.id,
            self.name,
            "Planner revision requested; this step will not be automatically replanned again in the same run.",
            message_type=MessageType.REVIEW,
            to_agent="PlannerAgent",
            step_id=step.id,
            structured_payload={"revision_requested": True, "loop_guard": "single_step_pause"},
        )
        record(
            "subagent.request_revision",
            from_agent,
            {"step": step.id, "tool_name": step.tool_name, "question": question},
            task_id=task.id,
        )

    def _friendly_tool_error(self, error: str) -> str:
        if "No authorized directories configured" in error:
            return "没有配置授权工作区。请先在设置里填写包含目标文件夹的授权工作区，然后再执行文件操作。"
        if "outside authorized directories" in error:
            return "目标路径不在授权工作区内。请先在设置里授权该路径的上级文件夹。"
        if "Sensitive or system paths" in error:
            return "目标路径属于系统或敏感路径，安全策略已阻止执行。"
        return f"任务执行失败：{error}" if error else "任务执行失败。"

    def _supervise_new_agent_messages(self, task_id: str, stage: str) -> bool:
        """Batch supervise new messages with per-task cursor and id de-dupe."""
        cache = self._supervised.setdefault(task_id, set())
        cursor = self._supervision_cursor.get(task_id)
        messages = self.bus.get_messages_after(task_id, cursor)
        if cursor is None:
            self._bootstrap_supervised_cache(task_id, cache, messages)
        pending = [
            message
            for message in messages
            if message.from_agent != self.safety.name and message.id not in cache
        ]
        if not pending:
            self._advance_supervision_cursor(task_id, messages)
            return True
        batch = self.safety.review_agent_messages_batch(pending, stage)
        for message_id in batch.supervised_message_ids:
            cache.add(message_id)
        supervised_pending = [message for message in pending if message.id in batch.supervised_message_ids]
        self._advance_supervision_cursor(task_id, supervised_pending)
        return batch.verdict != SafetyVerdict.DENY

    def _bootstrap_supervised_cache(self, task_id: str, cache: set[str], messages: list[AgentMessage] | None = None) -> None:
        for message in messages if messages is not None else self.bus.get_messages(task_id):
            if message.from_agent != self.safety.name:
                continue
            for supervised_id in message.metadata.get("supervised_message_ids") or []:
                cache.add(str(supervised_id))
            legacy = message.metadata.get("supervised_message_id")
            if legacy:
                cache.add(str(legacy))

    def _advance_supervision_cursor(self, task_id: str, messages: list[AgentMessage]) -> None:
        newest = max((message.created_at for message in messages if message.created_at), default="")
        if newest and newest > self._supervision_cursor.get(task_id, ""):
            self._supervision_cursor[task_id] = newest

    async def _recall_memory(self, goal: str) -> list:
        try:
            return await self.memory.recall(goal, k=3)
        except Exception as exc:
            record("memory.recall_failed", self.name, {"error": str(exc)})
            return []

    async def _consolidate_memory(self, task: Task, plan: Plan) -> None:
        summary = task.final_summary or f"Completed task: {task.user_goal}"
        try:
            await self.memory.remember(
                summary,
                task_id=task.id,
                kind="task_summary",
                tags=[step.agent_name for step in plan.steps if step.agent_name][:3],
                source=self.name,
            )
        except Exception as exc:
            record("memory.consolidate_failed", self.name, {"task_id": task.id, "error": str(exc)})

    async def _reflect_on_step(self, task: Task, step, result: ToolResult) -> None:
        try:
            tool = self.registry.get(step.tool_name) if step.tool_name else None
            owner_name = getattr(tool, "agent_owner", "") or step.agent_name
        except Exception:
            owner_name = step.agent_name
        agent = self.subagents.get(owner_name)
        if agent is None:
            return
        try:
            step_for_reflect = step
            step_for_reflect.task_id = step_for_reflect.task_id or task.id
            await agent.reflect(step_for_reflect, result)
        except Exception as exc:
            record("subagent.reflect_failed", agent.name, {"step": step.id, "error": str(exc)}, task_id=task.id)

    async def _consult_subagent(
        self,
        task: Task,
        step: PlanStep,
        *,
        observation: ToolResult | None = None,
    ) -> AgentAction | None:
        """Route a step to its owning subagent for autonomous reasoning.

        The subagent's AgentAction is published as a PROPOSAL message so the
        timeline shows the expert's decision before the tool actually runs.
        Returns the action so callers can apply tool proposals, pause on
        revision requests, or skip steps marked done.
        """
        tool = self.registry.get(step.tool_name) if step.tool_name else None
        owner_name = (getattr(tool, "agent_owner", "") or step.agent_name) if tool else step.agent_name
        agent = self.subagents.get(owner_name)
        if agent is None:
            return None
        context = AgentContext(
            task_id=task.id,
            mode=task.mode,
            allowed_directories=list(self._tool_context().get("allowed_directories") or []),
        )
        try:
            provider = get_provider(task="subagent")
        except Exception as exc:
            record("subagent.provider_failed", agent.name, {"step": step.id, "error": str(exc)}, task_id=task.id)
            return None
        try:
            action = await agent.act(step, context, observation=observation, provider=provider)
        except Exception as exc:
            record("subagent.act_failed", agent.name, {"step": step.id, "error": str(exc)}, task_id=task.id)
            return None
        diverged = bool(
            action.kind == "propose_tool"
            and action.tool_name
            and step.tool_name
            and action.tool_name != step.tool_name
        )
        rationale = (action.rationale or "").strip()
        summary_parts: list[str] = []
        if action.kind == "propose_tool":
            summary_parts.append(f"propose_tool {action.tool_name or step.tool_name}")
        elif action.kind == "request_revision":
            summary_parts.append("request_revision")
            if action.follow_up_question:
                summary_parts.append(f"follow_up: {action.follow_up_question[:160]}")
        else:
            summary_parts.append(action.kind)
        if rationale:
            summary_parts.append(rationale[:200])
        summary = " | ".join(summary_parts) or f"{agent.name} reasoned about {step.tool_name}"
        self.bus.publish_text(
            task.id,
            agent.name,
            summary,
            message_type=MessageType.PROPOSAL,
            step_id=step.id,
            structured_payload={"subagent_action": action.model_dump(), "diverged": diverged, "plan_tool": step.tool_name},
        )
        if diverged:
            record(
                "subagent.diverged_from_plan",
                agent.name,
                {"step": step.id, "plan_tool": step.tool_name, "proposed_tool": action.tool_name},
                task_id=task.id,
            )
        return action
