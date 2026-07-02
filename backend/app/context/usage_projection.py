from __future__ import annotations

from typing import Any

from app.config import AppSettings
from app.context.management import count_messages_tokens, project_messages_for_llm


def projection_summary(
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
        return {**projection, "summary": projection_brief(projection)}
    projection = project_messages_for_llm(
        messages,
        settings,
        session_context=session_context,
        source="context_usage",
        record_projection_event=False,
    )
    payload = {"enabled": True, **projection.to_dict()}
    return {**payload, "summary": projection_brief(payload)}


def projection_brief(projection: dict[str, Any]) -> dict[str, Any]:
    original_tokens = int_value(projection.get("original_tokens"))
    projected_tokens = int_value(projection.get("projected_tokens"))
    original_count = int_value(projection.get("original_count"))
    projected_count = int_value(projection.get("projected_count"))
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


def int_value(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
