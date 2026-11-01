from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from app.config import AppSettings
from app.context import compaction_observability as compaction_metrics
from app.context.agent_message_projection import strip_private_provenance
from app.context.compact_boundaries import (
    latest_compact_boundary as _latest_compact_boundary,
)
from app.context.compact_boundaries import (
    redact_compact_metadata,
)
from app.context.compact_boundaries import (
    retained_tail_message_ids as _retained_tail_message_ids,
)
from app.context.compaction_helpers import (
    _has_tool_protocol_messages,
    compact_boundary_view,
    repair_tool_message_invariants,
)
from app.context.compaction_strategies import (
    auto_compact_messages,
    inject_session_summary,
    snip_history_if_needed,
)
from app.context.micro_compact import (
    empty_micro_compact_metadata as _empty_micro_compact_metadata,
)
from app.context.micro_compact import (
    micro_compact_messages_with_metadata as _micro_compact_messages_with_metadata,
)
from app.context.micro_compact import (
    projection_compact_metadata as _projection_compact_metadata,
)
from app.context.tokens import (
    auto_compact_threshold,
    count_messages_tokens,
    warning_state,
)
from app.observability.best_effort import log_best_effort_failure

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ContextProjection:
    messages: list[dict[str, Any]]
    original_count: int
    projected_count: int
    original_tokens: int
    projected_tokens: int
    compacted: bool = False
    micro_compacted: bool = False
    history_snipped: bool = False
    session_summary_added: bool = False
    strategy: str = "none"
    source: str = "llm"
    boundary_id: str = ""
    compact_metadata: dict[str, Any] | None = None
    retained_tail_message_ids: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "original_count": self.original_count,
            "projected_count": self.projected_count,
            "original_tokens": self.original_tokens,
            "projected_tokens": self.projected_tokens,
            "compacted": self.compacted,
            "micro_compacted": self.micro_compacted,
            "history_snipped": self.history_snipped,
            "session_summary_added": self.session_summary_added,
            "strategy": self.strategy,
            "boundary_id": self.boundary_id,
            "compact_metadata": redact_compact_metadata(self.compact_metadata or {}),
            "retained_tail_message_ids": list(self.retained_tail_message_ids or []),
        }


def build_llm_request_snapshot(
    projection: ContextProjection,
    settings: AppSettings,
    *,
    task: str,
    purpose: str,
    provider: str,
    model: str,
    tools: list[dict[str, Any]] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_hash = hashlib.sha256(
        json.dumps(
            strip_private_provenance(projection.messages),
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    try:
        from app.policy.approval_binding import permission_policy_version
        from app.policy.permissions import PermissionStore

        policy_version = permission_policy_version(PermissionStore().updated_at())
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: context snapshots tolerate policy-store failures.
        log_best_effort_failure(logger, "context_snapshot.policy_version", exc, purpose=purpose)
        policy_version = ""
    return {
        "snapshot_id": f"ctx_{prompt_hash[:16]}",
        "prompt_hash": prompt_hash,
        "visible_tool_ids": _visible_tool_ids(tools),
        "policy_version": policy_version,
        "context_projection": projection.to_dict(),
        "routing": {
            "mode": settings.mode,
            "permission_mode": getattr(settings, "permission_mode", "default"),
            "task": task,
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "profile": profile or {},
        },
    }


def _visible_tool_ids(tools: list[dict[str, Any]] | None) -> list[str]:
    result: list[str] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name"):
            result.append(str(function.get("name")))
        elif tool.get("name"):
            result.append(str(tool.get("name")))
    return sorted({item for item in result if item})


@compaction_metrics.observe_projection_failures
def project_messages_for_llm(
    messages: list[dict[str, Any]],
    settings: AppSettings,
    *,
    session_context: dict[str, Any] | None = None,
    source: str = "llm",
    record_projection_event: bool = True,
) -> ContextProjection:
    from app.core.context_egress import filter_session_context

    session_context = filter_session_context(session_context, settings.mode)
    original = compact_boundary_view(_normalize_messages(messages))
    boundary = _latest_compact_boundary(original)
    original_tokens = count_messages_tokens(original)
    projected = list(original)
    micro_compacted = False
    micro_compact_metadata: dict[str, Any] = _empty_micro_compact_metadata()
    history_snipped = False
    session_summary_added = False
    auto_compact_attempted = auto_compact_applied = auto_reduced_tokens = False

    if settings.context_micro_compact_enabled:
        projected, micro_compacted, micro_compact_metadata = _micro_compact_messages_with_metadata(projected, settings)

    history_before_tokens = count_messages_tokens(projected)
    if settings.context_history_snip_enabled:
        projected, history_snipped = snip_history_if_needed(projected, settings)
    history_reduced_tokens = history_snipped and count_messages_tokens(projected) < history_before_tokens

    if (
        settings.context_session_memory_enabled
        and session_context
        and _should_inject_session_context(
            projected,
            session_context,
            settings,
        )
    ):
        projected, session_summary_added = inject_session_summary(projected, session_context, settings)

    projected_tokens = count_messages_tokens(projected)
    compacted = micro_compacted or history_snipped or session_summary_added
    if settings.context_auto_compact_enabled and projected_tokens >= auto_compact_threshold(settings):
        auto_compact_attempted = True
        before_auto_compact = projected
        projected, auto_reduced_tokens = auto_compact_messages(projected, settings, session_context=session_context)
        auto_compact_applied = projected != before_auto_compact
        compacted = compacted or auto_compact_applied
        projected_tokens = count_messages_tokens(projected)

    projected = (
        repair_tool_message_invariants(projected)
        if _has_tool_protocol_messages(projected)
        else [dict(message) for message in projected]
    )
    projected_tokens = count_messages_tokens(projected)

    projection = ContextProjection(
        messages=projected,
        original_count=len(original),
        projected_count=len(projected),
        original_tokens=original_tokens,
        projected_tokens=projected_tokens,
        compacted=compacted,
        micro_compacted=micro_compacted,
        history_snipped=history_snipped,
        session_summary_added=session_summary_added,
        strategy=_strategy(micro_compacted, history_snipped, session_summary_added, auto_compact_applied, compacted),
        source=source,
        boundary_id=str((boundary or {}).get("id") or ""),
        compact_metadata=_projection_compact_metadata(boundary or {}, micro_compact_metadata),
        retained_tail_message_ids=sorted(_retained_tail_message_ids(boundary or {})),
    )
    if projection.compacted and record_projection_event:
        _record_event(
            "context.projected",
            "ContextManager",
            {
                "source": source,
                "strategy": projection.strategy,
                "original_messages": projection.original_count,
                "projected_messages": projection.projected_count,
                "original_tokens": projection.original_tokens,
                "projected_tokens": projection.projected_tokens,
                "tokens_saved": max(0, projection.original_tokens - projection.projected_tokens),
            },
        )
    return compaction_metrics.record_projection_result(
        projection,
        enabled=record_projection_event,
        micro_applied=micro_compacted,
        history_snip_applied=history_snipped,
        auto_attempted=auto_compact_attempted,
        auto_applied=auto_compact_applied,
        token_reduced=bool(micro_compact_metadata.get("tokens_saved")) or history_reduced_tokens or auto_reduced_tokens,
    )


def project_ledger_for_llm(
    messages: list[dict[str, Any]],
    settings: AppSettings,
    *,
    session_context: dict[str, Any] | None = None,
    source: str = "agent_bus",
    record_projection_event: bool = True,
) -> ContextProjection:
    """Project the durable message ledger into a provider-safe prompt view.

    The ledger remains ``agent_messages``/OpenAI-like dicts. This adapter
    carries Lengrvis Code compact-boundary semantics through Lengrvis metadata
    rather than importing the TypeScript session runtime.
    """

    return project_messages_for_llm(
        messages,
        settings,
        session_context=session_context,
        source=source,
        record_projection_event=record_projection_event,
    )


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        item = dict(message)
        role = str(item.get("role") or "user")
        item["role"] = role
        if item.get("content") is None:
            item["content"] = ""
        normalized.append(item)
    return normalized


def _should_inject_session_context(
    messages: list[dict[str, Any]],
    session_context: dict[str, Any],
    settings: AppSettings,
) -> bool:
    if str(session_context.get("conversation_summary") or "").strip():
        return True
    return warning_state(count_messages_tokens(messages), settings).is_above_warning_threshold


def _strategy(
    micro_compacted: bool,
    history_snipped: bool,
    session_summary_added: bool,
    auto_compacted: bool,
    compacted: bool,
) -> str:
    parts: list[str] = []
    if micro_compacted:
        parts.append("micro")
    if history_snipped:
        parts.append("snip")
    if session_summary_added:
        parts.append("session")
    if auto_compacted:
        parts.append("auto")
    if compacted and not parts:
        parts.append("auto")
    return "+".join(parts) if parts else "none"


def _record_event(event_type: str, actor: str, payload: dict[str, Any] | None = None) -> None:
    try:
        from app.core.audit import record

        record(event_type, actor, payload or {})
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: audit failures are best-effort here.
        log_best_effort_failure(logger, "context.record_event", exc, actor=actor, event_type=event_type)


__all__ = [
    "ContextProjection",
    "build_llm_request_snapshot",
    "project_ledger_for_llm",
    "project_messages_for_llm",
]
