from __future__ import annotations

import inspect
from typing import Any

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.agents.planner_deterministic_intents import PlannerDeterministicIntentMixin
from app.agents.planner_deterministic_plans import PlannerDeterministicPlanMixin
from app.agents.worker_agents import normalize_supervisor_agent_hint
from app.core.schemas import MessageType, Plan, PlanStep
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.mock_provider import MockProvider
from app.llm.prompts import load_prompt, render_prompt
from app.llm.registry import get_effective_settings, get_provider
from app.orchestration.deterministic_contracts import seal_deterministic_plan
from app.perception.storage import is_sensitive_context
from app.policy.risk import RiskLevel, max_risk

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["goal", "steps"],
    "properties": {
        "goal": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "agent_name", "tool_name", "description", "args", "depends_on"],
                "properties": {
                    "id": {"type": "string"},
                    "agent_name": {"type": "string"},
                    "tool_name": {"type": "string"},
                    "description": {"type": "string"},
                    "args": {"type": "object"},
                    "expected_observation": {"type": "string"},
                    "risk_level": {"type": "string"},
                    "requires_approval": {"type": "boolean"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "rollback_strategy": {"type": "string"},
                },
            },
        },
    },
}


def supervisor_hint_allows_deterministic(agent_hint: str | None, owning_agent: str) -> bool:
    hint = normalize_supervisor_agent_hint(agent_hint)
    if not hint:
        return True
    return hint == owning_agent


def format_supervisor_hint_block(agent_hint: str | None) -> str:
    hint = normalize_supervisor_agent_hint(agent_hint)
    if not hint:
        return ""
    return (
        f"Supervisor routing hint: {hint}\n"
        f"Prefer tools owned by {hint} when they satisfy the user goal. "
        f"If another worker is clearly required, say so in assumptions.\n\n"
    )


def format_planner_revision_feedback_block(feedback: str | None) -> str:
    text = str(feedback or "").strip()
    if not text:
        return ""
    return f"Planner revision feedback:\n{text}\n\n"


# Context block truncation budgets (characters). Generous on purpose: the
# planner runs against large-window models (C1 calibrated 128k+) and starved
# memory/session snippets were costing plan quality far more than the tokens
# saved (playbook P4).
MEMORY_SNIPPET_CHARS = 600
SESSION_FIELD_CHARS = 600
SESSION_NOTE_CHARS = 360
CONVERSATION_SUMMARY_CHARS = 1200
GOAL_DESCRIPTION_CHARS = 480
SCREEN_DESCRIPTION_CHARS = 480


class PlannerAgent(
    PlannerDeterministicPlanMixin,
    PlannerDeterministicIntentMixin,
    BaseAgent,
):
    name = "PlannerAgent"
    prompt_file = "planner_agent.md"

    async def create_plan(
        self,
        task_id: str,
        goal: str,
        mode: str,
        tools: list[str],
        memory_context: list | None = None,
        perception_context: dict[str, Any] | None = None,
        goal_context: dict[str, Any] | str | None = None,
        session_context: dict[str, Any] | str | None = None,
        tool_specs: list[str] | None = None,
        agent_hint: str | None = None,
        planner_revision_feedback: str | None = None,
    ) -> Plan:
        for build_deterministic in (
            self._deterministic_large_files_plan,
            self._deterministic_duplicate_plan,
            self._deterministic_developer_status_plan,
            self._deterministic_developer_search_plan,
            self._deterministic_full_text_search_plan,
            self._deterministic_file_mutation_plan,
            self._deterministic_excel_write_plan,
            self._deterministic_cleanup_plan,
            self._deterministic_file_plan,
            self._deterministic_uninstall_plan,
            self._deterministic_browser_fill_plan,
            self._deterministic_browser_submit_plan,
            self._deterministic_browser_read_plan,
            self._deterministic_document_qa_plan,
            self._deterministic_document_read_plan,
            self._deterministic_memory_plan,
            self._deterministic_system_check_plan,
            self._deterministic_open_app_plan,
            self._deterministic_search_plan,
        ):
            deterministic_plan = build_deterministic(task_id, goal, tools, agent_hint=agent_hint)
            if deterministic_plan:
                seal_deterministic_plan(deterministic_plan)
                self._publish_plan(task_id, deterministic_plan)
                return deterministic_plan

        supervisor_hint_block = format_supervisor_hint_block(agent_hint)
        revision_feedback_block = format_planner_revision_feedback_block(planner_revision_feedback)
        memory_block = ""
        if memory_context:
            memory_lines = []
            for item in memory_context:
                content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else "")
                if content:
                    memory_lines.append(f"- {content[:MEMORY_SNIPPET_CHARS]}")
            if memory_lines:
                memory_block = "Past relevant memories:\n" + "\n".join(memory_lines) + "\n\n"
        goal_block = self._format_goal_context(goal_context)
        perception_block = self._format_perception_context(perception_context)
        session_block = self._format_session_context(session_context)
        context_blocks = memory_block + goal_block + perception_block + session_block
        if context_blocks:
            context_blocks += (
                "Context usage guidance: the blocks above are background signals. Use them to "
                "disambiguate the goal, reuse known paths and preferences, and avoid repeating "
                "finished work; lesson entries describe past failures you must not repeat. The "
                "'User goal' line below is always the source of truth.\n\n"
            )

        messages = [
            {
                "role": "system",
                "content": load_prompt("planner_agent.md"),
            },
            {
                "role": "user",
                "content": render_prompt(
                    "planner_user.md",
                    {
                        "memory_block": context_blocks,
                        "supervisor_hint_block": supervisor_hint_block,
                        "revision_feedback_block": revision_feedback_block,
                        "mode": mode,
                        "tools": "\n".join(f"- {entry}" for entry in (tool_specs or tools)),
                        "goal": goal,
                    },
                ),
            },
        ]
        settings = self._settings_for_mode(mode)
        effective_mode = (settings.mode or "efficiency").lower()
        allow_mock_fallback = bool(getattr(settings, "allow_mock_fallback", False))
        try:
            provider = self._provider_for_settings(settings)
            payload = await provider.structured_chat(messages, PLAN_SCHEMA)
        except LocalBackendUnavailable as exc:
            message = (
                f"Local LLM unavailable in privacy mode: {exc}"
                if effective_mode == "privacy"
                else f"Provider unavailable and MockProvider fallback is disabled: {exc}"
            )
            self.bus.publish_text(
                task_id,
                self.name,
                message,
                message_type=MessageType.REVISION,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
            if effective_mode == "privacy":
                self.bus.publish_text(
                    task_id,
                    self.name,
                    f"Local provider failed in privacy mode: {exc}",
                    message_type=MessageType.REVISION,
                )
                raise
            if not allow_mock_fallback:
                self.bus.publish_text(
                    task_id,
                    self.name,
                    f"Primary provider failed and MockProvider fallback is disabled: {exc}",
                    message_type=MessageType.REVISION,
                )
                raise
            self.bus.publish_text(
                task_id,
                self.name,
                f"Primary provider failed; using MockProvider fallback: {exc}",
                message_type=MessageType.REVISION,
            )
            payload = await MockProvider().structured_chat(messages, PLAN_SCHEMA)

        try:
            plan = self._payload_to_plan(task_id, payload)
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            if effective_mode == "privacy":
                self.bus.publish_text(
                    task_id,
                    self.name,
                    f"Local provider returned an invalid plan in privacy mode: {exc}",
                    message_type=MessageType.REVISION,
                )
                raise
            if not allow_mock_fallback:
                self.bus.publish_text(
                    task_id,
                    self.name,
                    f"Provider returned invalid plan and MockProvider fallback is disabled: {exc}",
                    message_type=MessageType.REVISION,
                )
                raise
            self.bus.publish_text(
                task_id,
                self.name,
                f"Provider returned invalid plan; using MockProvider fallback: {exc}",
                message_type=MessageType.REVISION,
            )
            fallback_payload = await MockProvider().structured_chat(messages, PLAN_SCHEMA)
            plan = self._payload_to_plan(task_id, fallback_payload)

        self._publish_plan(task_id, plan)
        return plan

    def _settings_for_mode(self, mode: str):
        return get_effective_settings().model_copy(update={"mode": mode or "efficiency"})

    def _provider_for_settings(self, settings):
        try:
            parameters = inspect.signature(get_provider).parameters
        except (TypeError, ValueError):
            parameters = {}
        if parameters:
            return get_provider(settings, task="planner")
        return get_provider()

    def _format_session_context(self, session_context: dict[str, Any] | str | None) -> str:
        if not session_context:
            return ""
        if isinstance(session_context, str):
            text = session_context.strip()
            return f"Session continuity context:\n{text}\n\n" if text else ""

        lines: list[str] = []
        workflow = session_context.get("current_workflow_state") or {}
        if workflow:
            lines.append(f"- Current workflow state: {str(workflow)[:SESSION_FIELD_CHARS]}")
        unfinished = list(session_context.get("unfinished_task_ids") or [])
        if unfinished:
            lines.append(f"- Unfinished tasks: {', '.join(str(item) for item in unfinished[:6])}")
        preferences = session_context.get("learned_preferences") or {}
        if preferences:
            lines.append(f"- Session preferences: {str(preferences)[:SESSION_FIELD_CHARS]}")
        notes = list(session_context.get("notes") or [])
        for note in notes[-5:]:
            text = str(note).strip()
            if text:
                lines.append(f"- Note: {text[:SESSION_NOTE_CHARS]}")
        conversation_summary = str(session_context.get("conversation_summary") or "").strip()
        if conversation_summary:
            lines.append(f"- Conversation summary: {conversation_summary[:CONVERSATION_SUMMARY_CHARS]}")
        if not lines:
            return ""
        return "Session continuity context:\n" + "\n".join(lines) + "\n\n"

    def _format_goal_context(self, goal_context: dict[str, Any] | str | None) -> str:
        if not goal_context:
            return ""
        if isinstance(goal_context, str):
            text = goal_context.strip()
            return f"Goal context:\n{text}\n\n" if text else ""

        lines: list[str] = []
        active_goal = goal_context.get("active_goal")
        if isinstance(active_goal, dict):
            description = str(active_goal.get("user_goal") or active_goal.get("description") or "").strip()
            if description:
                lines.append(f"- Active goal: {description[:GOAL_DESCRIPTION_CHARS]}")

        goal_stack = goal_context.get("goal_stack")
        if isinstance(goal_stack, list):
            for index, item in enumerate(goal_stack[-5:], start=1):
                if not isinstance(item, dict):
                    continue
                description = str(item.get("user_goal") or item.get("description") or "").strip()
                if description:
                    lines.append(f"- Goal {index}: {description[:GOAL_DESCRIPTION_CHARS]}")

        scope = str(goal_context.get("scope") or "").strip()
        if scope:
            lines.append(f"- Scope: {scope[:120]}")

        if not lines:
            return ""
        return "Goal context:\n" + "\n".join(lines) + "\n\n"

    def _format_perception_context(self, perception_context: dict[str, Any] | None) -> str:
        if not perception_context:
            return ""
        lines: list[str] = []
        screen_state = perception_context.get("screen_state")
        app_context = perception_context.get("app_context")
        if app_context is None and screen_state is not None:
            app_context = _context_value(screen_state, "app_context", None)
        if is_sensitive_context(screen_state=screen_state, app_context=app_context):
            return ""
        if screen_state is not None:
            description = str(_context_value(screen_state, "description") or "").strip()
            if description:
                lines.append(f"- Visible screen: {description[:SCREEN_DESCRIPTION_CHARS]}")
            tags = list(_context_value(screen_state, "tags", []) or [])
            if tags:
                lines.append(f"- Screen tags: {', '.join(str(tag) for tag in tags[:8])}")
        if app_context is not None:
            title = str(_context_value(app_context, "active_window_title") or "").strip()
            process = str(_context_value(app_context, "process_name") or "").strip()
            focused = _context_value(app_context, "focus_control", None)
            focus_name = (
                str(_context_value(focused, "name") or _context_value(focused, "text") or "").strip() if focused else ""
            )
            if title or process:
                lines.append(f"- Active app: {process or 'unknown'} / {title or 'untitled'}")
            if focus_name:
                lines.append(f"- Focused control: {focus_name[:160]}")
        if not lines:
            return ""
        return "Current perception context:\n" + "\n".join(lines) + "\n\n"

    def _publish_plan(self, task_id: str, plan: Plan) -> None:
        self.bus.publish_text(
            task_id,
            self.name,
            f"Generated plan with {len(plan.steps)} step(s).",
            structured_payload=plan.model_dump(),
        )

    def _payload_to_plan(self, task_id: str, payload: dict[str, Any]) -> Plan:
        steps: list[PlanStep] = []
        raw_steps = list(payload.get("steps", []))
        step_ids = self._stable_step_ids(raw_steps)
        id_aliases: dict[str, str] = {}
        for idx, raw in enumerate(raw_steps, start=1):
            provided_id = str(raw.get("id") or raw.get("step_id") or "").strip()
            if provided_id:
                id_aliases.setdefault(provided_id, step_ids[idx - 1])
            id_aliases.setdefault(f"step_{idx}", step_ids[idx - 1])

        for idx, raw in enumerate(raw_steps, start=1):
            try:
                risk = RiskLevel(str(raw.get("risk_level", "R0_READ_ONLY")))
            except ValueError:
                risk = RiskLevel.R0_READ_ONLY
            args = dict(raw.get("args") or {})
            if risk in {RiskLevel.R2_REVERSIBLE_MODIFY, RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM}:
                args["dry_run"] = True
            depends_on = self._normalize_depends_on(raw.get("depends_on"), id_aliases)
            step = PlanStep(
                id=step_ids[idx - 1],
                task_id=task_id,
                order=idx,
                agent_name=str(raw["agent_name"]),
                tool_name=str(raw["tool_name"]),
                description=str(raw.get("description", "")),
                args=args,
                expected_observation=str(raw.get("expected_observation", "")),
                risk_level=risk,
                requires_approval=bool(raw.get("requires_approval", risk.value.startswith(("R2", "R3")))),
                depends_on=depends_on,
                rollback_strategy=str(raw.get("rollback_strategy", "")),
            )
            steps.append(step)
        if not steps:
            raise ValueError("Plan must contain at least one step.")
        self._validate_step_dependencies(steps)
        global_risk = max_risk([step.risk_level for step in steps])
        return Plan(
            task_id=task_id,
            goal=str(payload.get("goal") or ""),
            assumptions=list(payload.get("assumptions") or []),
            steps=steps,
            global_risk_level=global_risk,
            requires_user_approval=any(step.requires_approval for step in steps),
        )

    def _stable_step_ids(self, raw_steps: list[dict[str, Any]]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for idx, raw in enumerate(raw_steps, start=1):
            candidate = str(raw.get("id") or raw.get("step_id") or "").strip() or f"step_{idx}"
            if candidate in seen:
                candidate = f"step_{idx}"
                suffix = 2
                while candidate in seen:
                    candidate = f"step_{idx}_{suffix}"
                    suffix += 1
            seen.add(candidate)
            result.append(candidate)
        return result

    def _normalize_depends_on(self, raw_value: Any, id_aliases: dict[str, str]) -> list[str]:
        if raw_value in (None, ""):
            return []
        raw_items = [raw_value] if isinstance(raw_value, str) else list(raw_value or [])
        result: list[str] = []
        for item in raw_items:
            dependency = str(item).strip()
            if not dependency:
                continue
            dependency = id_aliases.get(dependency, dependency)
            if dependency not in result:
                result.append(dependency)
        return result

    def _validate_step_dependencies(self, steps: list[PlanStep]) -> None:
        step_ids = {step.id for step in steps}
        for step in steps:
            missing = [dependency for dependency in step.depends_on if dependency not in step_ids]
            if missing:
                raise ValueError(f"Step {step.id} depends on unknown step id(s): {', '.join(missing)}")
            if step.id in step.depends_on:
                raise ValueError(f"Step {step.id} cannot depend on itself.")


def _context_value(value: Any, key: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
