from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.base import BaseAgent
from app.core.schemas import MessageType, Plan, PlanStep
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.mock_provider import MockProvider
from app.llm.prompts import load_prompt, render_prompt
from app.llm.registry import get_provider
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
    ) -> Plan:
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
                        "memory_block": memory_block,
                        "mode": mode,
                        "tools": "\n".join(f"- {tool}" for tool in tools),
                        "goal": goal,
                    },
                ),
            },
        ]
        try:
            provider = get_provider()
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
