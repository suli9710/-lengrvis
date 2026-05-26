from __future__ import annotations

import asyncio
from typing import Any

from app.agents.app_agent import AppAgent
from app.agents.base import AgentContext, BaseAgent
from app.agents.browser_agent import BrowserAgent
from app.agents.computer_agent import ComputerAgent
from app.agents.document_agent import DocumentAgent
from app.agents.file_agent import FileAgent
from app.agents.memory_agent import MemoryAgent
from app.agents.parallel_review_agent import ParallelReviewAgent
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
    ToolResult,
    now_iso,
)
from app.core.session_context import get_session_context_store
from app.llm.registry import get_effective_settings
from app.orchestration.agent_bus import AgentBus
from app.orchestration.dispatcher import EventDispatcher
from app.orchestration.handlers import (
    CompletionHandler,
    ConsultationHandler,
    PlanningHandler,
    RecoveryHandler,
    StepExecutionHandler,
    StepSchedulerHandler,
)
from app.orchestration.handlers.context import StepExecutionOutcome
from app.orchestration.goal_stack import GoalStack
from app.orchestration.state_machine import safe_transition
from app.orchestration.step_phase import set_step_status
from app.perception.context_store import handle_perception_event
from app.policy.risk import SafetyVerdict
from app.services.task_recording_service import capture_step_screenshot, recording_enabled
from app.tools.registry import register_all_tools


class OrchestratorAgent:
    name = "OrchestratorAgent"

    def __init__(self) -> None:
        self.bus = AgentBus()
        self.dispatcher = EventDispatcher(self.bus)
        self.planner = PlannerAgent(self.bus)
        self.safety = SafetyReviewAgent(self.bus)
        self.parallel_review = ParallelReviewAgent(self.bus)
        self.memory = MemoryAgent(self.bus)
        self.session_context_store = get_session_context_store()
        self.goal_stack = GoalStack(scope="default")
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
        self.planning_handler = PlanningHandler(self)
        self.consultation_handler = ConsultationHandler(self)
        self.step_scheduler_handler = StepSchedulerHandler(self)
        self.step_execution_handler = StepExecutionHandler(self)
        self.recovery_handler = RecoveryHandler(self, max_retries=max(0, get_effective_settings().recovery_max_retries))
        self.completion_handler = CompletionHandler(self)
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.planning_handler.register(self.dispatcher)
        self.consultation_handler.register(self.dispatcher)
        self.step_scheduler_handler.register(self.dispatcher)
        self.step_execution_handler.register(self.dispatcher)
        self.recovery_handler.register(self.dispatcher)
        self.completion_handler.register(self.dispatcher)
        self.dispatcher.register("perception.screen_state", handle_perception_event)

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
        return await self.planning_handler.run_task(task)

    async def _process_steps(self, task: Task, plan: Plan) -> None:
        await self.step_scheduler_handler.process_steps(task, plan)

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
        return await self.step_execution_handler.execute_step(
            task,
            plan,
            step,
            context,
            observation,
            threaded_tools=threaded_tools,
        )

    async def execute_approved_step(self, approval: Approval) -> Task:
        return await self.step_execution_handler.execute_approved_step(approval)

    def _latest_plan_for_task(self, task_id: str) -> Plan:
        plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
        if not plans:
            raise KeyError(f"Plan not found for task: {task_id}")
        return Plan.model_validate(plans[0])

    def _tool_context(self) -> dict:
        settings = get_effective_settings()
        return {"allowed_directories": settings.allowed_directories, "settings": settings, "registry": self.registry}

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
        return self.step_scheduler_handler._build_step_graph(plan)

    def _has_step_cycle(self, by_id: dict[str, PlanStep]) -> bool:
        return self.step_scheduler_handler._has_step_cycle(by_id)

    def _ready_steps(self, pending: set[str], by_id: dict[str, PlanStep]) -> list[PlanStep]:
        return self.step_scheduler_handler._ready_steps(pending, by_id)

    def _dependency_finished(self, step: PlanStep) -> bool:
        return self.step_scheduler_handler._dependency_finished(step)

    def _dependency_observation(self, step: PlanStep, observations: dict[str, ToolResult]) -> ToolResult | None:
        return self.step_scheduler_handler._dependency_observation(step, observations)

    def _mark_blocked_steps(self, pending: set[str], by_id: dict[str, PlanStep]) -> None:
        self.step_scheduler_handler._mark_blocked_steps(pending, by_id)

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
        set_step_status(step, StepStatus.SKIPPED, actor=self.name)
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
            memories = await self.memory.recall(goal, k=3)
            lessons = await self.memory.recall(goal, k=3, tags=["lesson"])
            seen: set[str] = set()
            combined = []
            for item in [*lessons, *memories]:
                item_id = getattr(item, "id", "")
                if item_id in seen:
                    continue
                seen.add(item_id)
                combined.append(item)
            return combined[:5]
        except Exception as exc:
            record("memory.recall_failed", self.name, {"error": str(exc)})
            return []

    async def _consolidate_memory(self, task: Task, plan: Plan) -> None:
        await self.completion_handler.consolidate_memory(task, plan)

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
            registry=self.registry,
        )
        try:
            action = await agent.act(step, context, observation=observation)
        except Exception as exc:
            record("subagent.act_failed", agent.name, {"step": step.id, "error": str(exc)}, task_id=task.id)
            return None
        if action is None:
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
