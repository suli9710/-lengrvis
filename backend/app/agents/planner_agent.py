from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan, PlanStep
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.mock_provider import MockProvider
from app.llm.prompts import load_prompt, render_prompt
from app.llm.registry import get_effective_settings, get_provider
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
                "required": ["id", "agent_name", "tool_name", "description", "args", "risk_level", "depends_on"],
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

DELETE_TERMS = ("delete", "remove", "trash", "删除", "删掉", "移除", "清理")
UNINSTALL_TERMS = ("uninstall", "卸载")
DRIVE_CLEANUP_RE = re.compile(r"(?P<drive>[a-zA-Z])\s*盘")
PATH_SUFFIXES = (
    " 这个文件夹",
    " 这个目录",
    " 这个文件",
    " 整个文件夹",
    " 文件夹",
    " 目录",
    " 文件",
)


class PlannerAgent(BaseAgent):
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
    ) -> Plan:
        deterministic_plan = self._deterministic_cleanup_plan(task_id, goal, tools)
        if deterministic_plan:
            self._publish_plan(task_id, deterministic_plan)
            return deterministic_plan
        deterministic_plan = self._deterministic_file_plan(task_id, goal, tools)
        if deterministic_plan:
            self._publish_plan(task_id, deterministic_plan)
            return deterministic_plan
        deterministic_plan = self._deterministic_uninstall_plan(task_id, goal, tools)
        if deterministic_plan:
            self._publish_plan(task_id, deterministic_plan)
            return deterministic_plan

        memory_block = ""
        if memory_context:
            memory_lines = []
            for item in memory_context:
                content = getattr(item, "content", None) or (item.get("content") if isinstance(item, dict) else "")
                if content:
                    memory_lines.append(f"- {content[:200]}")
            if memory_lines:
                memory_block = "Past relevant memories:\n" + "\n".join(memory_lines) + "\n\n"
        goal_block = self._format_goal_context(goal_context)
        perception_block = self._format_perception_context(perception_context)
        session_block = self._format_session_context(session_context)

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
                        "memory_block": memory_block + goal_block + perception_block + session_block,
                        "mode": mode,
                        "tools": "\n".join(f"- {tool}" for tool in tools),
                        "goal": goal,
                    },
                ),
            },
        ]
        try:
            provider = self._provider_for_mode(mode)
            payload = await provider.structured_chat(messages, PLAN_SCHEMA)
        except LocalBackendUnavailable as exc:
            self.bus.publish_text(
                task_id,
                self.name,
                f"Local LLM unavailable in privacy mode: {exc}",
                message_type=MessageType.REVISION,
            )
            raise
        except Exception as exc:
            if (mode or "privacy").lower() == "privacy":
                self.bus.publish_text(
                    task_id,
                    self.name,
                    f"Local provider failed in privacy mode: {exc}",
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
            if (mode or "privacy").lower() == "privacy":
                self.bus.publish_text(
                    task_id,
                    self.name,
                    f"Local provider returned an invalid plan in privacy mode: {exc}",
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

    def _provider_for_mode(self, mode: str):
        settings = dataclasses.replace(get_effective_settings(), mode=mode or "efficiency")
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
            lines.append(f"- Current workflow state: {str(workflow)[:240]}")
        unfinished = list(session_context.get("unfinished_task_ids") or [])
        if unfinished:
            lines.append(f"- Unfinished tasks: {', '.join(str(item) for item in unfinished[:6])}")
        preferences = session_context.get("learned_preferences") or {}
        if preferences:
            lines.append(f"- Session preferences: {str(preferences)[:240]}")
        notes = list(session_context.get("notes") or [])
        for note in notes[-5:]:
            text = str(note).strip()
            if text:
                lines.append(f"- Note: {text[:180]}")
        conversation_summary = str(session_context.get("conversation_summary") or "").strip()
        if conversation_summary:
            lines.append(f"- Conversation summary: {conversation_summary[:360]}")
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
                lines.append(f"- Active goal: {description[:240]}")

        goal_stack = goal_context.get("goal_stack")
        if isinstance(goal_stack, list):
            for index, item in enumerate(goal_stack[-5:], start=1):
                if not isinstance(item, dict):
                    continue
                description = str(item.get("user_goal") or item.get("description") or "").strip()
                if description:
                    lines.append(f"- Goal {index}: {description[:200]}")

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
                lines.append(f"- Visible screen: {description[:240]}")
            tags = list(_context_value(screen_state, "tags", []) or [])
            if tags:
                lines.append(f"- Screen tags: {', '.join(str(tag) for tag in tags[:8])}")
        if app_context is not None:
            title = str(_context_value(app_context, "active_window_title") or "").strip()
            process = str(_context_value(app_context, "process_name") or "").strip()
            focused = _context_value(app_context, "focus_control", None)
            focus_name = str(_context_value(focused, "name") or _context_value(focused, "text") or "").strip() if focused else ""
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

    def _deterministic_file_plan(self, task_id: str, goal: str, tools: list[str]) -> Plan | None:
        if "file.trash" not in tools or not self._has_delete_intent(goal):
            return None

        target_path = self._extract_windows_path(goal)
        if not target_path:
            return None

        step = PlanStep(
            id="step_1",
            task_id=task_id,
            order=1,
            agent_name="FileAgent",
            tool_name="file.trash",
            description=f"将指定路径移入回收站：{target_path}",
            args={"path": target_path, "dry_run": True},
            expected_observation="文件或文件夹已移入回收站。",
            risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            requires_approval=True,
            rollback_strategy="如需恢复，请从 Windows 回收站还原该项目。",
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["检测到明确的删除意图和 Windows 路径，因此使用确定性的文件删除计划。"],
            steps=[step],
            global_risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            requires_user_approval=True,
        )

    def _deterministic_cleanup_plan(self, task_id: str, goal: str, tools: list[str]) -> Plan | None:
        if "file.cleanup_plan" not in tools or not self._has_cleanup_intent(goal):
            return None
        if self._extract_windows_path(goal):
            return None

        roots = self._cleanup_roots(goal)
        if not roots:
            step = PlanStep(
                id="step_1",
                task_id=task_id,
                order=1,
                agent_name="FileAgent",
                tool_name="file.search_by_name",
                description="说明清理任务需要先设置授权目录。",
                args={"query": "清理文件前需要先在设置中添加要扫描的授权目录。"},
                expected_observation="已说明需要授权目录后才能扫描清理项。",
                risk_level=RiskLevel.R0_READ_ONLY,
                requires_approval=False,
                rollback_strategy="未执行文件修改。",
            )
            return Plan(
                task_id=task_id,
                goal=goal,
                assumptions=["用户提出了宽泛磁盘清理请求，但没有可用授权目录；不会把自然语言当作文件路径删除。"],
                steps=[step],
                global_risk_level=RiskLevel.R0_READ_ONLY,
                requires_user_approval=False,
            )

        step = PlanStep(
            id="step_1",
            task_id=task_id,
            order=1,
            agent_name="FileAgent",
            tool_name="file.cleanup_plan",
            description="扫描授权目录并生成清理预览。",
            args={"roots": roots, "threshold_mb": 50, "older_than_days": 30},
            expected_observation="已生成清理预览，所有删除或移入回收站操作都需要用户审批后才会执行。",
            risk_level=RiskLevel.R0_READ_ONLY,
            requires_approval=False,
            rollback_strategy="当前步骤只生成预览，不修改文件。",
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["检测到宽泛清理请求；先扫描授权目录生成清理预览，不直接删除文件。"],
            steps=[step],
            global_risk_level=RiskLevel.R0_READ_ONLY,
            requires_user_approval=False,
        )

    def _deterministic_uninstall_plan(self, task_id: str, goal: str, tools: list[str]) -> Plan | None:
        if "app.uninstall_app" not in tools or not self._has_uninstall_intent(goal):
            return None

        query = self._extract_uninstall_query(goal)
        if not query:
            return None

        step = PlanStep(
            id="step_1",
            task_id=task_id,
            order=1,
            agent_name="AppAgent",
            tool_name="app.uninstall_app",
            description=f"查找并启动应用卸载程序：{query}",
            args={"query": query, "dry_run": True},
            expected_observation="应用卸载程序已启动，等待用户完成厂商卸载向导。",
            risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            requires_approval=True,
            rollback_strategy="卸载由应用自身安装器处理；如需恢复需重新安装该应用。",
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["检测到明确的应用卸载意图，因此先定位卸载项并等待用户审批。"],
            steps=[step],
            global_risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            requires_user_approval=True,
        )

    def _has_delete_intent(self, goal: str) -> bool:
        normalized = goal.lower()
        return any(term in normalized for term in DELETE_TERMS)

    def _has_cleanup_intent(self, goal: str) -> bool:
        normalized = goal.lower()
        return "清理" in normalized or "cleanup" in normalized or "clean up" in normalized

    def _cleanup_roots(self, goal: str) -> list[str]:
        settings_roots = [str(path) for path in get_effective_settings().allowed_directories or []]
        drive = self._extract_drive_root(goal)
        if drive:
            normalized_drive = drive.casefold().rstrip("\\/")
            matching_roots = [
                root
                for root in settings_roots
                if str(Path(root).drive).casefold().rstrip("\\/") == normalized_drive
            ]
            return matching_roots or settings_roots
        return settings_roots

    def _extract_drive_root(self, goal: str) -> str | None:
        match = DRIVE_CLEANUP_RE.search(goal)
        if not match:
            return None
        return f"{match.group('drive').upper()}:"

    def _has_uninstall_intent(self, goal: str) -> bool:
        normalized = goal.lower()
        return any(term in normalized for term in UNINSTALL_TERMS)

    def _extract_uninstall_query(self, goal: str) -> str:
        query = goal.strip()
        for term in ("帮我", "请", "一下", "应用", "软件", "程序"):
            query = query.replace(term, "")
        for term in ("卸载", "uninstall"):
            query = re.sub(re.escape(term), "", query, flags=re.IGNORECASE)
        return query.strip(" ：:，,。.")

    def _extract_windows_path(self, goal: str) -> str | None:
        quoted = re.search(r"[\"“](?P<path>[A-Za-z]:[\\/][^\"”]+)[\"”]", goal)
        if quoted:
            return self._clean_path_candidate(quoted.group("path"))

        match = re.search(r"(?P<path>[A-Za-z]:[\\/][^\r\n\"<>|?*]+)", goal)
        if not match:
            return None
        return self._clean_path_candidate(match.group("path"))

    def _clean_path_candidate(self, value: str) -> str:
        candidate = value.strip().strip("`'\"“”‘’")
        candidate = candidate.rstrip("。.,，;；、)]}）")
        for suffix in PATH_SUFFIXES:
            if candidate.endswith(suffix):
                candidate = candidate[: -len(suffix)].rstrip()

        if Path(candidate).exists():
            return str(Path(candidate).resolve(strict=False))

        parts = candidate.split()
        while len(parts) > 1:
            shortened = " ".join(parts[:-1]).rstrip("。.,，;；、)]}）")
            if Path(shortened).exists():
                return str(Path(shortened).resolve(strict=False))
            parts = parts[:-1]
        return candidate

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
            risk = RiskLevel(str(raw.get("risk_level", "R0_READ_ONLY")))
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
