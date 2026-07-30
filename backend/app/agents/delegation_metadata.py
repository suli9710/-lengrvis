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

_DOCUMENT_SOURCE_RE = re.compile(r"\.(?:md|txt|pdf|docx|pptx|xlsx?)\b", re.IGNORECASE)
_DOCUMENT_SOURCE_TERMS = ("ocr", "office", "模板", "文档", "pdf", "docx", "pptx")
_DOCUMENT_OPERATION_TERMS = ("总结", "摘要", "读取", "提取", "回答", "引用", "解析", "summarize", "extract")
_EXCEL_OPERATION_TERMS = ("excel", "工作簿", "单元格")
_DEVELOPER_CONTEXT_TERMS = ("git", "pytest", "代码", "仓库", "code", "codebase", "repo", "repository", "mcp")
_LARGE_FILE_TERMS = ("大文件", "占空间最大的文件", "占用空间最大的文件", "large files", "largest files")
_FILE_CREATION_TERMS = ("新建", "创建", "写入", "create", "write")
_MEMORY_CONTROL_TERMS = ("偏好", "记忆", "记住", "memory", "preference", "remember")
_MEMORY_MUTATION_TERMS = (
    "保存",
    "存入",
    "写入",
    "记录",
    "记住",
    "撤销",
    "停用",
    "作废",
    "忘记",
    "save",
    "store",
    "remember",
    "revoke",
    "disable",
    "forget",
)
_QUOTED_USER_CONTENT_RE = re.compile(r'“[^”]*”|"[^"\r\n]*"')
_MEMORY_NON_PERSISTENCE_PATTERNS = (
    re.compile(r"(?:不要|不得|不能|不应|别|勿)(?:再|继续)?\s*(?:记住|记下)"),
    re.compile(
        r"(?:不要|不得|不能|不应|别|勿)(?:把|将)?"
        r"[^。；;，,\r\n]{0,20}(?:写入|保存|存入|记录|晋升(?:为)?|变更)"
        r"[^。；;，,\r\n]{0,20}(?:记忆|偏好)"
    ),
    re.compile(
        r"\b(?:do\s+not|don't|never)\s+"
        r"(?:store|save|remember|record|promote|change)\b",
        re.IGNORECASE,
    ),
)

# A Windows workspace path can contain words such as ``pytest``, ``repo`` or
# ``code``.  Those are file names, not developer intent; strip path literals
# before the broad context heuristics while retaining the original text for
# file/document routing below.
_PATH_LITERAL_RE = re.compile(r"(?<![\w])(?:[a-z]:[\\/]|/)[^\s,，。；;]+", re.IGNORECASE)


def _strip_path_literals(text: str) -> str:
    return _PATH_LITERAL_RE.sub(" ", text)


def _is_strong_document_goal(text_lower: str) -> bool:
    has_source = bool(_DOCUMENT_SOURCE_RE.search(text_lower)) or contains_any(text_lower, _DOCUMENT_SOURCE_TERMS)
    return has_source and contains_any(text_lower, _DOCUMENT_OPERATION_TERMS)


def is_memory_non_persistence_goal(goal: str) -> bool:
    text_lower = goal.strip().lower()
    if not contains_any(text_lower, _MEMORY_CONTROL_TERMS):
        return False
    unquoted_text = _QUOTED_USER_CONTENT_RE.sub("", text_lower)
    return any(pattern.search(unquoted_text) for pattern in _MEMORY_NON_PERSISTENCE_PATTERNS)


def is_explicit_memory_mutation_goal(goal: str) -> bool:
    text_lower = goal.strip().lower()
    return (
        bool(text_lower)
        and not is_memory_non_persistence_goal(text_lower)
        and contains_any(text_lower, _MEMORY_CONTROL_TERMS)
        and contains_any(text_lower, _MEMORY_MUTATION_TERMS)
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
    intent_text = _strip_path_literals(text)
    intent_lower = intent_text.lower()
    if goal_is_system_diagnostics(intent_text):
        return "ComputerAgent"
    if is_memory_non_persistence_goal(intent_lower):
        return ""
    if is_explicit_memory_mutation_goal(intent_lower):
        return "MemoryAgent"
    if contains_any(intent_lower, _EXCEL_OPERATION_TERMS):
        return "AppAgent"
    if contains_any(intent_lower, _DEVELOPER_CONTEXT_TERMS):
        return "ComputerAgent"
    if contains_any(intent_lower, _LARGE_FILE_TERMS):
        return "ComputerAgent"
    if contains_any(lower, _FILE_CREATION_TERMS) and contains_any(lower, FILE_TARGET_TERMS):
        return "FileAgent"
    if _is_strong_document_goal(lower):
        return "DocumentAgent"
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
