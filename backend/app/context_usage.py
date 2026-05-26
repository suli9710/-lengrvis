from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.config import AppSettings
from app.context_management import (
    auto_compact_threshold,
    compact_boundary_view,
    count_messages_tokens,
    effective_context_window,
    repair_tool_message_invariants,
    rough_token_count,
    warning_state,
)
from app.core import db
from app.core.schemas import AgentMessage
from app.core.session_context import get_session_context_store
from app.llm.registry import get_effective_settings
from app.tools.registry import registry as tool_registry


SYSTEM_CONTEXT_CATEGORY = "system_context_messages"
TOOLS_REGISTRY_CATEGORY = "tools_registry"
MCP_TOOLS_CATEGORY = "mcp_tools"
SESSION_MEMORY_CATEGORY = "session_memory"
AGENT_HISTORY_CATEGORY = "agent_messages_history"
FREE_SPACE_CATEGORY = "free_space"
AUTO_COMPACT_BUFFER_CATEGORY = "auto_compact_buffer"
MANUAL_COMPACT_BUFFER_CATEGORY = "manual_compact_buffer"


@dataclass(frozen=True, slots=True)
class ContextUsageCategory:
    id: str
    label: str
    tokens: int
    percent: float
    item_count: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextUsageReport:
    total_tokens: int
    used_tokens: int
    free_tokens: int
    effective_context_window: int
    model_context_window: int
    auto_compact_threshold: int
    manual_compact_limit: int
    warning: dict[str, Any]
    categories: list[ContextUsageCategory]


def analyze_context_usage(
    *,
    messages: Iterable[dict[str, Any]] | None = None,
    system_context_messages: Iterable[dict[str, Any]] | None = None,
    tool_definitions: Iterable[Any] | None = None,
    mcp_tools: Iterable[Any] | None = None,
    session_context: dict[str, Any] | None = None,
    settings: AppSettings | None = None,
    task_id: str | None = None,
    include_registered_tools: bool = True,
    include_session_memory: bool = True,
) -> ContextUsageReport:
    """Estimate how the current prompt context is distributed by source.

    The analyzer intentionally uses the same rough counters and compact
    thresholds as ``context_management`` so route output lines up with the
    compaction behavior used by LLM calls.
    """

    resolved_settings = settings or get_effective_settings()
    explicit_messages = list(messages or [])
    system_messages = list(system_context_messages or [])
    if task_id and not explicit_messages:
        explicit_messages = load_agent_history(task_id)
    explicit_messages = repair_tool_message_invariants(compact_boundary_view(explicit_messages))

    system_from_history = [message for message in explicit_messages if _is_system_message(message)]
    agent_history = [message for message in explicit_messages if not _is_system_message(message)]
    all_system_messages = [*system_messages, *system_from_history]

    if tool_definitions is None and include_registered_tools:
        tool_definitions = tool_registry.list()
    local_tools, registry_mcp_tools = _split_tool_definitions(tool_definitions or [])
    explicit_mcp_tools = list(mcp_tools or [])
    all_mcp_tools = [*registry_mcp_tools, *explicit_mcp_tools]

    if session_context is None and include_session_memory:
        session_context = _current_session_context()

    effective_window = effective_context_window(resolved_settings)
    auto_threshold = min(effective_window, auto_compact_threshold(resolved_settings))
    manual_limit = max(1, effective_window - max(0, int(resolved_settings.context_manual_compact_buffer_tokens)))

    base_rows = [
        _category(
            SYSTEM_CONTEXT_CATEGORY,
            "System/context messages",
            count_messages_tokens(all_system_messages),
            effective_window,
            item_count=len(all_system_messages),
        ),
        _category(
            TOOLS_REGISTRY_CATEGORY,
            "Tools/registry",
            _count_tools_tokens(local_tools),
            effective_window,
            item_count=len(local_tools),
            details={"deferred_count": _count_tool_attr(local_tools, "defer_loading", True)},
        ),
        _category(
            MCP_TOOLS_CATEGORY,
            "MCP tools",
            _count_tools_tokens(all_mcp_tools),
            effective_window,
            item_count=len(all_mcp_tools),
        ),
        _category(
            SESSION_MEMORY_CATEGORY,
            "Session memory",
            rough_token_count(session_context or {}),
            effective_window,
            item_count=_session_item_count(session_context),
        ),
        _category(
            AGENT_HISTORY_CATEGORY,
            "Agent messages/history",
            count_messages_tokens(agent_history),
            effective_window,
            item_count=len(agent_history),
        ),
    ]
    used_tokens = sum(item.tokens for item in base_rows)
    manual_buffer_tokens = max(0, effective_window - manual_limit)
    auto_buffer_tokens = max(0, manual_limit - auto_threshold) if resolved_settings.context_auto_compact_enabled else 0
    free_tokens = max(0, effective_window - used_tokens - manual_buffer_tokens - auto_buffer_tokens)

    categories = [
        *base_rows,
        _category(FREE_SPACE_CATEGORY, "Free space", free_tokens, effective_window),
        _category(
            AUTO_COMPACT_BUFFER_CATEGORY,
            "Auto compact buffer",
            auto_buffer_tokens,
            effective_window,
            details={"enabled": resolved_settings.context_auto_compact_enabled},
        ),
        _category(MANUAL_COMPACT_BUFFER_CATEGORY, "Manual compact buffer", manual_buffer_tokens, effective_window),
    ]
    state = warning_state(used_tokens, resolved_settings)
    return ContextUsageReport(
        total_tokens=sum(item.tokens for item in categories),
        used_tokens=used_tokens,
        free_tokens=free_tokens,
        effective_context_window=effective_window,
        model_context_window=max(1, int(resolved_settings.model_context_window or 1)),
        auto_compact_threshold=auto_threshold,
        manual_compact_limit=manual_limit,
        warning={
            "token_count": state.token_count,
            "threshold": state.threshold,
            "percent_left": state.percent_left,
            "is_above_warning_threshold": state.is_above_warning_threshold,
            "is_above_error_threshold": state.is_above_error_threshold,
            "is_above_auto_compact_threshold": state.is_above_auto_compact_threshold,
            "is_at_blocking_limit": state.is_at_blocking_limit,
        },
        categories=categories,
    )


def context_usage_to_dict(report: ContextUsageReport) -> dict[str, Any]:
    return {
        "total_tokens": report.total_tokens,
        "used_tokens": report.used_tokens,
        "free_tokens": report.free_tokens,
        "effective_context_window": report.effective_context_window,
        "model_context_window": report.model_context_window,
        "auto_compact_threshold": report.auto_compact_threshold,
        "manual_compact_limit": report.manual_compact_limit,
        "warning": report.warning,
        "categories": [
            {
                "id": category.id,
                "label": category.label,
                "tokens": category.tokens,
                "percent": category.percent,
                "item_count": category.item_count,
                "details": category.details,
            }
            for category in report.categories
        ],
    }


def load_agent_history(task_id: str, *, limit: int = 1000) -> list[dict[str, Any]]:
    rows = db.fetch_many("agent_messages", "task_id = ?", (task_id,), limit=limit)
    messages = [AgentMessage.model_validate(row).to_openai_dict(include_legacy=False) for row in rows]
    return sorted(messages, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))


def _category(
    category_id: str,
    label: str,
    tokens: int,
    window: int,
    *,
    item_count: int = 0,
    details: dict[str, Any] | None = None,
) -> ContextUsageCategory:
    normalized_tokens = max(0, int(tokens or 0))
    return ContextUsageCategory(
        id=category_id,
        label=label,
        tokens=normalized_tokens,
        percent=round((normalized_tokens / max(1, window)) * 100, 2),
        item_count=max(0, int(item_count or 0)),
        details=details or {},
    )


def _current_session_context() -> dict[str, Any]:
    try:
        return get_session_context_store().planning_context()
    except Exception:  # noqa: BLE001
        return {}


def _is_system_message(message: dict[str, Any]) -> bool:
    role = str(message.get("role") or "").lower()
    return role in {"system", "developer"}


def _split_tool_definitions(tools: Iterable[Any]) -> tuple[list[Any], list[Any]]:
    local: list[Any] = []
    mcp: list[Any] = []
    for tool in tools:
        name = _tool_name(tool)
        if name.startswith("mcp."):
            mcp.append(tool)
        else:
            local.append(tool)
    return local, mcp


def _count_tools_tokens(tools: Iterable[Any]) -> int:
    return rough_token_count([_tool_payload(tool) for tool in tools])


def _tool_payload(tool: Any) -> dict[str, Any]:
    if hasattr(tool, "name"):
        return {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", ""),
            "input_schema": getattr(tool, "input_schema", {}),
            "output_schema": getattr(tool, "output_schema", {}),
            "risk_level": str(getattr(tool, "risk_level", "")),
            "agent_owner": getattr(tool, "agent_owner", ""),
            "search_hint": getattr(tool, "search_hint", ""),
        }
    if isinstance(tool, dict):
        return {
            "name": tool.get("name") or "",
            "description": tool.get("description") or "",
            "input_schema": tool.get("input_schema") or tool.get("inputSchema") or {},
            "output_schema": tool.get("output_schema") or tool.get("outputSchema") or {},
            "server": tool.get("server") or "",
        }
    return {"name": str(tool)}


def _tool_name(tool: Any) -> str:
    if hasattr(tool, "name"):
        return str(getattr(tool, "name") or "")
    if isinstance(tool, dict):
        server = str(tool.get("server") or "")
        name = str(tool.get("name") or "")
        return f"mcp.{server}.{name}" if server and not name.startswith("mcp.") else name
    return str(tool)


def _count_tool_attr(tools: Iterable[Any], attr: str, value: Any) -> int:
    return sum(1 for tool in tools if getattr(tool, attr, None) == value)


def _session_item_count(session_context: dict[str, Any] | None) -> int:
    if not session_context:
        return 0
    count = 0
    for value in session_context.values():
        if isinstance(value, (list, tuple, set, dict)):
            count += len(value)
        elif value:
            count += 1
    return count
