"""Source policy for persisted context reused by a planner."""

from __future__ import annotations

import hmac
from typing import Any

from app.core.content_provenance import stable_content_hash
from app.core.schemas import Memory
from app.policy.approval_binding import hmac_digest


def summary_context_policy(session_id: str, summary: str, *, cloud_allowed: bool) -> dict[str, Any]:
    policy = {"session_id": session_id, "content_hash": stable_content_hash(summary), "cloud_allowed": cloud_allowed}
    return {**policy, "hmac": hmac_digest(policy, prefix="session-summary-context/v1")}


def summary_context_allowed(context: dict[str, Any], destination_mode: str) -> bool:
    if destination_mode == "privacy":
        return True
    policy = context.get("_conversation_summary_policy")
    if not isinstance(policy, dict) or policy.get("cloud_allowed") is not True:
        return False
    expected = summary_context_policy(
        str(context.get("session_id") or ""), str(context.get("conversation_summary") or ""), cloud_allowed=True
    )
    return hmac.compare_digest(str(policy.get("hmac") or ""), expected["hmac"])


def filter_session_context(context: dict[str, Any] | None, destination_mode: str) -> dict[str, Any] | None:
    if not context or summary_context_allowed(context, destination_mode):
        return context
    return {
        **context,
        "conversation_summary": "",
        "_conversation_summary_envelope": None,
    }


def planner_context_allowed(source_mode: str, destination_mode: str, *, cloud_allowed: bool) -> bool:
    # Both efficiency and hybrid planners can send their entire payload to cloud.
    return destination_mode == "privacy" or (source_mode in {"efficiency", "hybrid"} and cloud_allowed)


def memory_context_policy_hmac(memory: Memory) -> str:
    return hmac_digest(
        {
            "memory_id": memory.id,
            "content_hash": stable_content_hash(memory.content),
            "source_mode": memory.source_mode,
            "allow_cloud_context": memory.allow_cloud_context,
        },
        prefix="memory-context/v1",
    )


def memory_context_allowed(memory: Memory, destination_mode: str) -> bool:
    if not planner_context_allowed(memory.source_mode, destination_mode, cloud_allowed=memory.allow_cloud_context):
        return False
    if destination_mode == "privacy":
        return True
    # Legacy records without an authenticated source policy remain local.
    return bool(memory.context_policy_hmac) and hmac.compare_digest(
        memory.context_policy_hmac, memory_context_policy_hmac(memory)
    )
