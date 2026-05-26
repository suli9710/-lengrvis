from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.config import AppSettings
from app.context_management import (
    auto_compact_threshold,
    compact_boundary_view,
    count_message_tokens,
    count_messages_tokens,
    effective_context_window,
    project_messages_for_llm,
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
    projection: dict[str, Any] = field(default_factory=dict)
    phases: list[dict[str, Any]] = field(default_factory=list)
    breakdown: dict[str, Any] = field(default_factory=dict)
    claude_view: dict[str, Any] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    lineage: dict[str, Any] = field(default_factory=dict)
    reserved_output_tokens: int = 0


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
    include_projection: bool = True,
) -> ContextUsageReport:
    """Estimate how the current prompt context is distributed by source.

    The analyzer intentionally uses the same rough counters and compact
    thresholds as ``context_management`` so route output lines up with the
    compaction behavior used by LLM calls.
    """

    resolved_settings = settings or get_effective_settings()
    explicit_messages = list(messages or [])
    system_messages = list(system_context_messages or [])
    loaded_history_from_task = bool(task_id and not explicit_messages)
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
            details={"breakdown": _message_breakdown(all_system_messages)},
        ),
        _category(
            TOOLS_REGISTRY_CATEGORY,
            "Tools/registry",
            _count_tools_tokens(local_tools),
            effective_window,
            item_count=len(local_tools),
            details={
                "deferred_count": _count_tool_attr(local_tools, "defer_loading", True),
                "breakdown": _tools_breakdown(local_tools),
            },
        ),
        _category(
            MCP_TOOLS_CATEGORY,
            "MCP tools",
            _count_tools_tokens(all_mcp_tools),
            effective_window,
            item_count=len(all_mcp_tools),
            details={"breakdown": _tools_breakdown(all_mcp_tools)},
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
            details={"breakdown": _message_breakdown(agent_history)},
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
    projection = _projection_summary(
        explicit_messages,
        resolved_settings,
        session_context=session_context,
        include_projection=include_projection,
    )
    message_breakdown = _message_breakdown(explicit_messages)
    category_totals = {category.id: category.tokens for category in categories}
    phases = _phases(
        base_rows=base_rows,
        projection=projection,
        used_tokens=used_tokens,
        free_tokens=free_tokens,
        auto_buffer_tokens=auto_buffer_tokens,
        manual_buffer_tokens=manual_buffer_tokens,
        effective_window=effective_window,
    )
    breakdown = {
        "categories": category_totals,
        "messages": message_breakdown,
        "tools": {
            "registered_tokens": _count_tools_tokens(local_tools),
            "registered_count": len(local_tools),
            "mcp_tokens": _count_tools_tokens(all_mcp_tools),
            "mcp_count": len(all_mcp_tools),
            "deferred_count": _count_tool_attr(local_tools, "defer_loading", True),
        },
    }
    warning = {
        "token_count": state.token_count,
        "threshold": state.threshold,
        "percent_left": state.percent_left,
        "is_above_warning_threshold": state.is_above_warning_threshold,
        "is_above_error_threshold": state.is_above_error_threshold,
        "is_above_auto_compact_threshold": state.is_above_auto_compact_threshold,
        "is_at_blocking_limit": state.is_at_blocking_limit,
    }
    health = _health_summary(
        used_tokens=used_tokens,
        free_tokens=free_tokens,
        effective_window=effective_window,
        warning=warning,
        projection=projection,
    )
    lineage = _lineage_summary(
        task_id=task_id,
        explicit_messages=explicit_messages,
        all_system_messages=all_system_messages,
        agent_history=agent_history,
        local_tools=local_tools,
        mcp_tools=all_mcp_tools,
        session_context=session_context,
        include_registered_tools=include_registered_tools,
        include_session_memory=include_session_memory,
        include_projection=include_projection,
        loaded_history_from_task=loaded_history_from_task,
        base_rows=base_rows,
        projection=projection,
    )

    return ContextUsageReport(
        total_tokens=sum(item.tokens for item in categories),
        used_tokens=used_tokens,
        free_tokens=free_tokens,
        effective_context_window=effective_window,
        model_context_window=max(1, int(resolved_settings.model_context_window or 1)),
        auto_compact_threshold=auto_threshold,
        manual_compact_limit=manual_limit,
        warning=warning,
        categories=categories,
        projection=projection,
        phases=phases,
        breakdown=breakdown,
        health=health,
        lineage=lineage,
        reserved_output_tokens=max(0, max(1, int(resolved_settings.model_context_window or 1)) - effective_window),
        claude_view=_claude_view(
            categories=categories,
            breakdown=message_breakdown,
            settings=resolved_settings,
            used_tokens=used_tokens,
            effective_window=effective_window,
            model_context_window=max(1, int(resolved_settings.model_context_window or 1)),
            auto_threshold=auto_threshold,
            mcp_tools=all_mcp_tools,
        ),
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
        "reserved_output_tokens": report.reserved_output_tokens,
        "warning": report.warning,
        "projection": report.projection,
        "phases": report.phases,
        "breakdown": report.breakdown,
        "claude_view": report.claude_view,
        "health": report.health,
        "lineage": report.lineage,
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


def _projection_summary(
    messages: list[dict[str, Any]],
    settings: AppSettings,
    *,
    session_context: dict[str, Any] | None,
    include_projection: bool,
) -> dict[str, Any]:
    if not include_projection:
        token_count = count_messages_tokens(messages)
        projection = {
            "enabled": False,
            "original_count": len(messages),
            "projected_count": len(messages),
            "original_tokens": token_count,
            "projected_tokens": token_count,
            "compacted": False,
            "strategy": "none",
        }
        return {**projection, "summary": _projection_brief(projection)}
    projection = project_messages_for_llm(
        messages,
        settings,
        session_context=session_context,
        source="context_usage",
        record_projection_event=False,
    )
    payload = {"enabled": True, **projection.to_dict()}
    return {**payload, "summary": _projection_brief(payload)}


def _projection_brief(projection: dict[str, Any]) -> dict[str, Any]:
    original_tokens = _int_value(projection.get("original_tokens"))
    projected_tokens = _int_value(projection.get("projected_tokens"))
    original_count = _int_value(projection.get("original_count"))
    projected_count = _int_value(projection.get("projected_count"))
    compacted = bool(projection.get("compacted"))
    adjustments: list[str] = []
    if projection.get("micro_compacted"):
        adjustments.append("micro_compacted")
    if projection.get("history_snipped"):
        adjustments.append("history_snipped")
    if projection.get("session_summary_added"):
        adjustments.append("session_summary_added")
    if compacted and not adjustments:
        adjustments.append("compacted")

    tokens_saved = max(0, original_tokens - projected_tokens)
    messages_removed = max(0, original_count - projected_count)
    strategy = str(projection.get("strategy") or "none")
    description = "Projection keeps the prompt unchanged."
    if not bool(projection.get("enabled")):
        description = "Projection is disabled for this usage estimate."
    elif compacted:
        description = "Projection trims context before the provider call."
    elif projection.get("session_summary_added"):
        description = "Projection adds session continuity context."

    return {
        "enabled": bool(projection.get("enabled")),
        "strategy": strategy,
        "compacted": compacted,
        "original_tokens": original_tokens,
        "projected_tokens": projected_tokens,
        "tokens_saved": tokens_saved,
        "messages_removed": messages_removed,
        "adjustments": adjustments,
        "description": description,
    }


def _health_summary(
    *,
    used_tokens: int,
    free_tokens: int,
    effective_window: int,
    warning: dict[str, Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    projected_tokens = _int_value(projection.get("projected_tokens"), used_tokens)
    projected_free_tokens = max(0, effective_window - projected_tokens)
    used_percent = round((max(0, used_tokens) / max(1, effective_window)) * 100, 2)
    projected_percent = round((max(0, projected_tokens) / max(1, effective_window)) * 100, 2)

    status = "healthy"
    severity = "ok"
    reason = "Context has comfortable room."
    if bool(warning.get("is_at_blocking_limit")):
        status = "blocked"
        severity = "error"
        reason = "Context is at the manual compaction limit."
    elif bool(warning.get("is_above_error_threshold")):
        status = "critical"
        severity = "error"
        reason = "Context is very close to the compact threshold."
    elif bool(warning.get("is_above_warning_threshold")):
        status = "watch"
        severity = "warning"
        reason = "Context is getting close to compaction."
    elif bool(projection.get("compacted")):
        status = "managed"
        severity = "ok"
        reason = "Projection already trimmed context before the provider call."

    return {
        "status": status,
        "severity": severity,
        "reason": reason,
        "used_percent": used_percent,
        "free_percent": max(0, round(100 - used_percent, 2)),
        "free_tokens": max(0, free_tokens),
        "projected_tokens": projected_tokens,
        "projected_percent": projected_percent,
        "projected_free_tokens": projected_free_tokens,
        "is_healthy": severity == "ok",
    }


def _lineage_summary(
    *,
    task_id: str | None,
    explicit_messages: list[dict[str, Any]],
    all_system_messages: list[dict[str, Any]],
    agent_history: list[dict[str, Any]],
    local_tools: list[Any],
    mcp_tools: list[Any],
    session_context: dict[str, Any] | None,
    include_registered_tools: bool,
    include_session_memory: bool,
    include_projection: bool,
    loaded_history_from_task: bool,
    base_rows: list[ContextUsageCategory],
    projection: dict[str, Any],
) -> dict[str, Any]:
    categories = {
        row.id: {
            "tokens": row.tokens,
            "item_count": row.item_count,
        }
        for row in base_rows
    }
    message_roles: dict[str, int] = {}
    for message in explicit_messages:
        role = str(message.get("role") or "unknown")
        message_roles[role] = message_roles.get(role, 0) + 1

    return {
        "task_id": task_id or "",
        "history_source": "task_history" if loaded_history_from_task else "request_payload",
        "message_count": len(explicit_messages),
        "system_message_count": len(all_system_messages),
        "agent_message_count": len(agent_history),
        "message_roles": message_roles,
        "local_tool_count": len(local_tools),
        "mcp_tool_count": len(mcp_tools),
        "session_memory_item_count": _session_item_count(session_context),
        "include_registered_tools": bool(include_registered_tools),
        "include_session_memory": bool(include_session_memory),
        "include_projection": bool(include_projection),
        "categories": categories,
        "projection": {
            "source": str(projection.get("source") or "context_usage"),
            "strategy": str(projection.get("strategy") or "none"),
            "boundary_id": str(projection.get("boundary_id") or ""),
            "retained_tail_count": len(list(projection.get("retained_tail_message_ids") or [])),
        },
    }


def _message_breakdown(messages: list[dict[str, Any]]) -> dict[str, Any]:
    tool_use_id_to_name: dict[str, str] = {}
    tool_call_tokens = 0
    tool_calls_by_type: dict[str, dict[str, int]] = {}
    tool_result_tokens = 0
    tool_results_by_type: dict[str, int] = {}
    attachment_tokens = 0
    attachments_by_type: dict[str, int] = {}
    assistant_tokens = 0
    user_tokens = 0

    for message in messages:
        role = str(message.get("role") or "")
        if role == "assistant":
            content_tokens = rough_token_count(message.get("content"))
            calls = list(message.get("tool_calls") or [])
            if calls:
                call_tokens = rough_token_count(calls)
                tool_call_tokens += call_tokens
                for call in calls:
                    name = _tool_call_name(call)
                    call_id = str(call.get("id") or "").strip() if isinstance(call, dict) else ""
                    if call_id:
                        tool_use_id_to_name[call_id] = name
                    item = tool_calls_by_type.setdefault(name, {"callTokens": 0, "resultTokens": 0})
                    item["callTokens"] += rough_token_count(call)
            else:
                assistant_tokens += content_tokens + 4
            continue
        if role == "tool":
            tokens = count_message_tokens(message)
            tool_result_tokens += tokens
            name = tool_use_id_to_name.get(str(message.get("tool_call_id") or "").strip(), "unknown")
            tool_results_by_type[name] = tool_results_by_type.get(name, 0) + tokens
            item = tool_calls_by_type.setdefault(name, {"callTokens": 0, "resultTokens": 0})
            item["resultTokens"] += tokens
            continue
        attachments = _attachment_breakdown(message.get("content"))
        if attachments:
            attachment_tokens += sum(tokens for _name, tokens in attachments)
            for name, tokens in attachments:
                attachments_by_type[name] = attachments_by_type.get(name, 0) + tokens
        if role == "user":
            user_tokens += count_message_tokens(message)
        elif role not in {"system", "developer"}:
            assistant_tokens += count_message_tokens(message)

    return {
        "toolCallTokens": tool_call_tokens,
        "toolResultTokens": tool_result_tokens,
        "attachmentTokens": attachment_tokens,
        "assistantMessageTokens": assistant_tokens,
        "userMessageTokens": user_tokens,
        "toolCallsByType": [
            {"name": name, "callTokens": values["callTokens"], "resultTokens": values["resultTokens"]}
            for name, values in sorted(
                tool_calls_by_type.items(),
                key=lambda item: item[1]["callTokens"] + item[1]["resultTokens"],
                reverse=True,
            )
        ],
        "attachmentsByType": [
            {"name": name, "tokens": tokens}
            for name, tokens in sorted(attachments_by_type.items(), key=lambda item: item[1], reverse=True)
        ],
        "toolResultsByType": [
            {"name": name, "tokens": tokens}
            for name, tokens in sorted(tool_results_by_type.items(), key=lambda item: item[1], reverse=True)
        ],
    }


def _phases(
    *,
    base_rows: list[ContextUsageCategory],
    projection: dict[str, Any],
    used_tokens: int,
    free_tokens: int,
    auto_buffer_tokens: int,
    manual_buffer_tokens: int,
    effective_window: int,
) -> list[dict[str, Any]]:
    rows = [
        {"id": "assemble", "label": "Assemble prompt sources", "tokens": sum(row.tokens for row in base_rows)},
        {
            "id": "projection",
            "label": "Project provider prompt",
            "tokens": int(projection.get("projected_tokens") or used_tokens),
            "details": {
                "strategy": projection.get("strategy", "none"),
                "compacted": bool(projection.get("compacted")),
            },
        },
        {
            "id": "reserve",
            "label": "Reserve compaction buffers",
            "tokens": auto_buffer_tokens + manual_buffer_tokens,
            "details": {"auto": auto_buffer_tokens, "manual": manual_buffer_tokens},
        },
        {"id": "free_space", "label": "Remaining context", "tokens": free_tokens},
    ]
    return [
        {
            **row,
            "percent": round((max(0, int(row.get("tokens") or 0)) / max(1, effective_window)) * 100, 2),
            "details": row.get("details", {}),
        }
        for row in rows
    ]


def _claude_view(
    *,
    categories: list[ContextUsageCategory],
    breakdown: dict[str, Any],
    settings: AppSettings,
    used_tokens: int,
    effective_window: int,
    model_context_window: int,
    auto_threshold: int,
    mcp_tools: list[Any],
) -> dict[str, Any]:
    category_names = {
        SYSTEM_CONTEXT_CATEGORY: "System prompt",
        TOOLS_REGISTRY_CATEGORY: "System tools",
        MCP_TOOLS_CATEGORY: "MCP tools",
        SESSION_MEMORY_CATEGORY: "Memory files",
        AGENT_HISTORY_CATEGORY: "Messages",
        FREE_SPACE_CATEGORY: "Free space",
        AUTO_COMPACT_BUFFER_CATEGORY: "Reserved",
        MANUAL_COMPACT_BUFFER_CATEGORY: "Manual compact buffer",
    }
    return {
        "categories": [
            {
                "name": category_names.get(category.id, category.label),
                "tokens": category.tokens,
                "color": _category_color(category.id),
                "isDeferred": bool(category.details.get("deferred")) if category.details else False,
            }
            for category in categories
        ],
        "totalTokens": used_tokens,
        "maxTokens": effective_window,
        "rawMaxTokens": model_context_window,
        "percentage": round((used_tokens / max(1, effective_window)) * 100, 2),
        "model": settings.model,
        "memoryFiles": [],
        "mcpTools": [
            {
                "name": _tool_name(tool).split(".")[-1],
                "serverName": _tool_server_name(tool),
                "tokens": rough_token_count(_tool_payload(tool)),
                "isLoaded": True,
            }
            for tool in mcp_tools
        ],
        "agents": [],
        "autoCompactThreshold": auto_threshold,
        "isAutoCompactEnabled": bool(settings.context_auto_compact_enabled),
        "messageBreakdown": breakdown,
        "apiUsage": {
            "input_tokens": used_tokens,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }


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


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _tool_call_name(tool_call: Any) -> str:
    if not isinstance(tool_call, dict):
        return "unknown"
    function = tool_call.get("function") or {}
    if isinstance(function, dict) and function.get("name"):
        return str(function.get("name"))
    return str(tool_call.get("name") or tool_call.get("type") or "unknown")


def _attachment_breakdown(content: Any) -> list[tuple[str, int]]:
    if not isinstance(content, list):
        return []
    result: list[tuple[str, int]] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        block_type = str(item.get("type") or "")
        if block_type in {"image", "image_url", "document", "input_audio"}:
            result.append((block_type, rough_token_count(item)))
    return result


def _tool_name(tool: Any) -> str:
    if hasattr(tool, "name"):
        return str(getattr(tool, "name") or "")
    if isinstance(tool, dict):
        server = str(tool.get("server") or "")
        name = str(tool.get("name") or "")
        return f"mcp.{server}.{name}" if server and not name.startswith("mcp.") else name
    return str(tool)


def _tool_server_name(tool: Any) -> str:
    if isinstance(tool, dict):
        server = str(tool.get("server") or "")
        if server:
            return server
        name = str(tool.get("name") or "")
        parts = name.split(".")
        if len(parts) >= 3 and parts[0] == "mcp":
            return parts[1]
    name = _tool_name(tool)
    parts = name.split(".")
    if len(parts) >= 3 and parts[0] == "mcp":
        return parts[1]
    return ""


def _category_color(category_id: str) -> str:
    return {
        SYSTEM_CONTEXT_CATEGORY: "system",
        TOOLS_REGISTRY_CATEGORY: "tools",
        MCP_TOOLS_CATEGORY: "mcp",
        SESSION_MEMORY_CATEGORY: "memory",
        AGENT_HISTORY_CATEGORY: "messages",
        FREE_SPACE_CATEGORY: "promptBorder",
        AUTO_COMPACT_BUFFER_CATEGORY: "inactive",
        MANUAL_COMPACT_BUFFER_CATEGORY: "inactive",
    }.get(category_id, "default")


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


def _tools_breakdown(tools: Iterable[Any]) -> dict[str, Any]:
    by_tool: list[dict[str, Any]] = []
    by_server: dict[str, int] = {}
    for tool in tools:
        payload = _tool_payload(tool)
        tokens = rough_token_count(payload)
        name = str(payload.get("name") or "")
        server = str(payload.get("server") or "")
        if server:
            by_server[server] = by_server.get(server, 0) + tokens
        by_tool.append({"name": name, "tokens": tokens, "server": server})
    return {
        "by_tool": by_tool,
        "by_server": by_server,
        "loaded_tokens": sum(item["tokens"] for item in by_tool),
        "deferred_tokens": 0,
    }
