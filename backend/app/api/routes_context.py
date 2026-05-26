from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.context_compaction import compact_session_context, compact_task_context, manual_compact_messages, manual_compact_result_to_dict
from app.context_usage import analyze_context_usage, context_usage_to_dict


router = APIRouter()


class ContextUsageRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    system_context_messages: list[dict[str, Any]] = Field(default_factory=list)
    tools: list[dict[str, Any]] | None = None
    mcp_tools: list[dict[str, Any]] = Field(default_factory=list)
    session_context: dict[str, Any] | None = None
    task_id: str | None = None
    include_registered_tools: bool = True
    include_session_memory: bool = True
    include_projection: bool = True


class ManualCompactRequest(BaseModel):
    task_id: str | None = None
    messages: list[dict[str, Any]] = Field(default_factory=list)
    custom_instructions: str = ""
    recent_message_limit: int | None = None
    persist_session_context: bool = True
    persist_agent_boundary: bool = True


@router.get("/context/usage")
def current_context_usage(task_id: str | None = None, include_projection: bool = True) -> dict[str, Any]:
    report = analyze_context_usage(task_id=task_id, include_projection=include_projection)
    return context_usage_to_dict(report)


@router.post("/context/usage")
def estimate_context_usage(payload: ContextUsageRequest) -> dict[str, Any]:
    report = analyze_context_usage(
        messages=payload.messages,
        system_context_messages=payload.system_context_messages,
        tool_definitions=payload.tools,
        mcp_tools=payload.mcp_tools,
        session_context=payload.session_context,
        task_id=payload.task_id,
        include_registered_tools=payload.include_registered_tools,
        include_session_memory=payload.include_session_memory,
        include_projection=payload.include_projection,
    )
    return context_usage_to_dict(report)


@router.post("/context/compact")
def compact_context(payload: ManualCompactRequest) -> dict[str, Any]:
    if payload.task_id:
        result = compact_task_context(
            payload.task_id,
            custom_instructions=payload.custom_instructions,
            recent_message_limit=payload.recent_message_limit,
            persist_session_context=payload.persist_session_context,
            persist_agent_boundary=payload.persist_agent_boundary,
        )
        return manual_compact_result_to_dict(result)
    if payload.persist_session_context:
        result = compact_session_context(
            payload.messages,
            custom_instructions=payload.custom_instructions,
            recent_message_limit=payload.recent_message_limit,
        )
    else:
        result = manual_compact_messages(
            payload.messages,
            custom_instructions=payload.custom_instructions,
            recent_message_limit=payload.recent_message_limit,
        )
    return manual_compact_result_to_dict(result)
