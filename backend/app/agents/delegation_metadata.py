from __future__ import annotations

import re
from typing import Any

from app.agents.delegation_rules import (
    APP_HINT_TERMS,
    BROWSER_HINT_TERMS,
    DOCUMENT_HINT_TERMS,
    FILE_ACTION_TERMS,
    FILE_FALLBACK_TERMS,
    FILE_TARGET_TERMS,
    ORGANIZE_TERMS,
    SEARCH_HINT_TERMS,
    URL_TERMS,
    contains_any,
    goal_is_system_diagnostics,
)
from app.agents.worker_agents import normalize_supervisor_agent_hint

DEVELOPER_ENGINE_CAPABILITY_MODE = "read_only_code_analysis"
DEVELOPER_ENGINE_WRITE_MODE = "controlled_code_editing"

DEVELOPER_ENGINE_DISCLOSURE = (
    "Developer Engine 仅做只读代码分析（Read/Grep/Glob/受限 git·pytest），"
    "不会自动修改仓库文件。"
    "带修复/实现意图的请求默认走电脑执行引擎并审批；"
    "如需在 workspace 内受控写代码，请启用 developer_writes_enabled。"
)

DEVELOPER_ENGINE_WRITE_DISCLOSURE = (
    "Developer Engine 已启用受控写能力（Write/Edit，仅限 workspace）。"
    "每次写操作经 Lengrvis Code 权限确认（dry-run/审批）；"
    "完成后自动做 git diff 预览与 pytest 验证闭环；仍禁止 Bash/Agent 与 skip-permissions。"
)

DEVELOPER_WRITE_ROUTED_TO_OS_DISCLOSURE = (
    "该请求含代码修复/实现意图，当前未启用 developer_writes_enabled，"
    "已路由到电脑执行引擎（审批 + file.edit_text / 工具链），"
    "不会直接在 Developer Engine 内改仓库。"
)


class SupervisorHintPlanError(Exception):
    """Planner produced steps outside the supervisor-hint tool surface."""


def plan_tools_outside_visible(plan, visible_tool_names: list[str]) -> list[str]:
    allowed = set(visible_tool_names)
    return [step.tool_name for step in plan.steps if step.tool_name not in allowed]


def plan_matches_supervisor_hint(plan, agent_hint: str | None, visible_tool_names: list[str] | None = None) -> bool:
    hint = normalize_supervisor_agent_hint(agent_hint)
    if not hint or not plan.steps:
        return True
    if visible_tool_names is not None and plan_tools_outside_visible(plan, visible_tool_names):
        return False
    return True


def build_task_delegation_metadata(
    *,
    agent_hint: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(extra or {})
    existing = normalize_supervisor_agent_hint(merged.pop("supervisor_agent_hint", None))
    hint = normalize_supervisor_agent_hint(agent_hint) or existing
    if hint:
        merged["supervisor_agent_hint"] = hint
    return merged


def infer_supervisor_agent_hint(goal: str) -> str:
    """Best-effort worker hint for OS runs that bypass Supervisor chat."""
    text = goal.strip()
    if not text:
        return ""
    lower = text.lower()
    if goal_is_system_diagnostics(text):
        return "ComputerAgent"
    if contains_any(lower, URL_TERMS) or contains_any(lower, BROWSER_HINT_TERMS):
        return "BrowserAgent"
    if contains_any(lower, SEARCH_HINT_TERMS) and not contains_any(lower, FILE_TARGET_TERMS):
        return "SearchAgent"
    if contains_any(lower, ORGANIZE_TERMS) and contains_any(lower, FILE_TARGET_TERMS + ("发票",)):
        return "FileAgent"
    if contains_any(lower, FILE_ACTION_TERMS) and (
        contains_any(lower, FILE_TARGET_TERMS) or re.search(r"\.(txt|pdf|docx|pptx)\b", lower)
    ):
        return "FileAgent"
    if contains_any(lower, DOCUMENT_HINT_TERMS):
        return "DocumentAgent"
    if contains_any(lower, APP_HINT_TERMS):
        return "AppAgent"
    if contains_any(lower, FILE_FALLBACK_TERMS):
        return "FileAgent"
    return ""


def merge_run_task_metadata(
    *,
    agent_hint: str | None = None,
    task_metadata: dict[str, Any] | None = None,
    goal: str = "",
) -> dict[str, Any]:
    merged = build_task_delegation_metadata(agent_hint=agent_hint, extra=task_metadata)
    if not merged.get("supervisor_agent_hint") and goal.strip():
        inferred = infer_supervisor_agent_hint(goal)
        if inferred:
            merged["supervisor_agent_hint"] = inferred
    return merged


def developer_engine_capabilities(*, writes_enabled: bool = False) -> dict[str, Any]:
    if writes_enabled:
        return {
            "writes_enabled": True,
            "mode": DEVELOPER_ENGINE_WRITE_MODE,
            "disclosure": DEVELOPER_ENGINE_WRITE_DISCLOSURE,
        }
    return {
        "writes_enabled": False,
        "mode": DEVELOPER_ENGINE_CAPABILITY_MODE,
        "disclosure": DEVELOPER_ENGINE_DISCLOSURE,
    }


def os_engine_capabilities(*, route_rule: str = "") -> dict[str, Any]:
    caps: dict[str, Any] = {"writes_enabled": False, "mode": "os_execution"}
    if route_rule == "developer_write_os":
        caps["disclosure"] = DEVELOPER_WRITE_ROUTED_TO_OS_DISCLOSURE
    return caps
