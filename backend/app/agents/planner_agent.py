from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.agents.path_detection import find_explicit_path
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

DELETE_TERMS = ("delete", "remove", "trash", "删除", "删掉", "移除", "清理")
UNINSTALL_TERMS = ("uninstall", "卸载")
SYSTEM_CHECK_TERMS = (
    "检查电脑状态",
    "检查这台电脑",
    "电脑状态",
    "系统体检",
    "运行状态",
    "关键进程",
    "本地 ai",
    "本地ai",
    "computer status",
    "system status",
    "diagnostics",
)
DRIVE_CLEANUP_RE = re.compile(r"(?P<drive>[a-zA-Z])\s*盘")
OPEN_APP_EXCLUDE_TERMS = (
    "文件",
    "目录",
    "网站",
    "网页",
    "链接",
    "http",
    "www.",
    ".com",
    ".cn",
    ".net",
    ".org",
    "file",
    "folder",
    "directory",
    "website",
    "url",
)
# Whole-query aliases so common Chinese app names hit the launch allowlist.
OPEN_APP_NAME_ALIASES = {"记事本": "notepad", "计算器": "calculator"}
PATH_SUFFIXES = (
    " 这个文件夹",
    " 这个目录",
    " 这个文件",
    " 整个文件夹",
    " 文件夹",
    " 目录",
    " 文件",
)

from app.agents.worker_agents import KNOWN_SUPERVISOR_WORKER_AGENTS, normalize_supervisor_agent_hint


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
        tool_specs: list[str] | None = None,
        agent_hint: str | None = None,
        planner_revision_feedback: str | None = None,
    ) -> Plan:
        for build_deterministic in (
            self._deterministic_cleanup_plan,
            self._deterministic_file_plan,
            self._deterministic_uninstall_plan,
            self._deterministic_system_check_plan,
            self._deterministic_open_app_plan,
            self._deterministic_search_plan,
        ):
            deterministic_plan = build_deterministic(task_id, goal, tools, agent_hint=agent_hint)
            if deterministic_plan:
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
        except Exception as exc:
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
        return dataclasses.replace(get_effective_settings(), mode=mode or "efficiency")

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

    def _deterministic_file_plan(self, task_id: str, goal: str, tools: list[str], *, agent_hint: str | None = None) -> Plan | None:
        if not supervisor_hint_allows_deterministic(agent_hint, "FileAgent"):
            return None
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
            assumptions=["检测到明确的删除意图和路径，因此使用确定性的文件删除计划。"],
            steps=[step],
            global_risk_level=RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
            requires_user_approval=True,
        )

    def _deterministic_cleanup_plan(self, task_id: str, goal: str, tools: list[str], *, agent_hint: str | None = None) -> Plan | None:
        if not supervisor_hint_allows_deterministic(agent_hint, "FileAgent"):
            return None
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

    def _deterministic_uninstall_plan(self, task_id: str, goal: str, tools: list[str], *, agent_hint: str | None = None) -> Plan | None:
        if not supervisor_hint_allows_deterministic(agent_hint, "AppAgent"):
            return None
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

    def _deterministic_system_check_plan(self, task_id: str, goal: str, tools: list[str], *, agent_hint: str | None = None) -> Plan | None:
        if not supervisor_hint_allows_deterministic(agent_hint, "ComputerAgent"):
            return None
        if "system.diagnostics" not in tools or not self._has_system_check_intent(goal):
            return None

        step = PlanStep(
            id="step_1",
            task_id=task_id,
            order=1,
            agent_name="ComputerAgent",
            tool_name="system.diagnostics",
            description="只读检查系统、磁盘、关键进程和本地 AI 状态。",
            args={},
            expected_observation="已完成只读电脑状态检查，未修改系统设置或文件。",
            risk_level=RiskLevel.R0_READ_ONLY,
            requires_approval=False,
            rollback_strategy="当前步骤只读取状态，不修改系统，无需回滚。",
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["检测到电脑状态检查请求；使用确定性只读系统诊断计划，不需要 LLM 规划。"],
            steps=[step],
            global_risk_level=RiskLevel.R0_READ_ONLY,
            requires_user_approval=False,
        )

    def _deterministic_open_app_plan(self, task_id: str, goal: str, tools: list[str], *, agent_hint: str | None = None) -> Plan | None:
        if not supervisor_hint_allows_deterministic(agent_hint, "AppAgent"):
            return None
        if "app.launch_installed" not in tools or not self._has_open_app_intent(goal):
            return None
        if self._has_delete_intent(goal) or self._has_uninstall_intent(goal) or self._has_system_check_intent(goal):
            return None
        if self._extract_windows_path(goal):
            return None

        app_query = self._extract_open_app_query(goal)
        if not app_query or len(app_query) > 60:
            return None

        step = PlanStep(
            id="step_1",
            task_id=task_id,
            order=1,
            agent_name="AppAgent",
            tool_name="app.launch_installed",
            description=f"启动本机已安装的应用：{app_query}",
            args={"app": app_query},
            expected_observation="目标应用已启动；只允许打开允许列表或已安装应用，不做其他系统修改。",
            risk_level=RiskLevel.R1_OPEN_ONLY,
            requires_approval=False,
            rollback_strategy="如不需要该应用，请手动关闭其窗口；本步骤不修改文件或系统设置。",
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["检测到明确的打开应用意图，因此使用确定性的应用启动计划。"],
            steps=[step],
            global_risk_level=RiskLevel.R1_OPEN_ONLY,
            requires_user_approval=False,
        )

    def _deterministic_search_plan(self, task_id: str, goal: str, tools: list[str], *, agent_hint: str | None = None) -> Plan | None:
        if not supervisor_hint_allows_deterministic(agent_hint, "FileAgent"):
            return None
        if "file.search_by_name" not in tools or not self._has_file_search_intent(goal):
            return None
        normalized = goal.casefold()
        if "重复" in goal or "duplicate" in normalized:
            return None
        if self._has_delete_intent(goal) or self._has_cleanup_intent(goal) or self._has_uninstall_intent(goal):
            return None
        if self._extract_windows_path(goal):
            return None

        query = self._extract_search_query(goal)
        if not query or len(query) > 80:
            return None

        step = PlanStep(
            id="step_1",
            task_id=task_id,
            order=1,
            agent_name="FileAgent",
            tool_name="file.search_by_name",
            description=f"在授权目录中按文件名搜索：{query}",
            args={"query": query},
            expected_observation="已返回授权目录内匹配该名称的文件列表，未修改任何文件。",
            risk_level=RiskLevel.R0_READ_ONLY,
            requires_approval=False,
            rollback_strategy="当前步骤只读搜索，不修改文件，无需回滚。",
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["检测到明确的按文件名搜索意图，因此使用确定性的只读搜索计划。"],
            steps=[step],
            global_risk_level=RiskLevel.R0_READ_ONLY,
            requires_user_approval=False,
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

    def _has_open_app_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        has_open_verb = "打开" in goal or "启动" in goal or re.search(r"\b(open|launch)\b", normalized) is not None
        if not has_open_verb:
            return False
        return not any(term in normalized for term in OPEN_APP_EXCLUDE_TERMS)

    def _extract_open_app_query(self, goal: str) -> str:
        query = goal.strip()
        for term in ("帮我", "请", "麻烦", "一下", "这个", "应用", "软件", "程序"):
            query = query.replace(term, "")
        for term in ("打开", "启动"):
            query = query.replace(term, "")
        query = re.sub(r"\b(open|launch|the|app|application)\b", "", query, flags=re.IGNORECASE)
        query = query.strip(" ：:，,。.!！?？\"'“”‘’")
        return OPEN_APP_NAME_ALIASES.get(query.casefold(), query)

    def _has_file_search_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        if re.search(r"\b(find|search|locate)\b.*\bfiles?\b", normalized):
            return True
        if re.search(r"\bfiles?\b.*\b(named|called)\b", normalized):
            return True
        return "文件" in goal and ("找" in goal or "搜" in goal)

    def _extract_search_query(self, goal: str) -> str:
        text = goal.strip()
        colon_match = re.search(r"[:：](?P<q>.+)$", text)
        if colon_match:
            candidate = colon_match.group("q")
        else:
            candidate = text
            for term in ("帮我", "请", "麻烦", "一下", "所有", "相关"):
                candidate = candidate.replace(term, "")
            for term in ("查找", "搜索", "找到", "寻找", "搜", "找"):
                candidate = candidate.replace(term, "")
            candidate = re.sub(r"\b(find|search( for)?|locate|named|called|files?|the)\b", "", candidate, flags=re.IGNORECASE)
            candidate = candidate.replace("文件名", "").replace("文件", "")
        return candidate.strip(" ：:，,。.\"'“”‘’")


    def _has_system_check_intent(self, goal: str) -> bool:
        normalized = goal.casefold()
        if any(term.casefold() in normalized for term in SYSTEM_CHECK_TERMS):
            return True
        return (
            "检查" in goal
            and ("电脑" in goal or "系统" in goal)
            and any(term in goal for term in ("状态", "磁盘", "内存", "进程", "可用性"))
        )

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

        match = find_explicit_path(goal)
        if not match:
            return None
        return self._clean_path_candidate(match)

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
