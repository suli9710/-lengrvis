"""Process-trusted execution marker for validated live writes.

The orchestrator (`tool_runtime.execute_allowed`) and the direct API routes
validate an approval (HMAC binding + atomic claim) before running a side-effecting
tool. They then stamp the execution ``context`` with a process-unique marker via
:func:`mark_execution_approved`. Tool/runtime write paths call
:func:`execution_is_marked_approved` so that bare ``approved``/``approval_id``
flags in the *arguments* alone can no longer authorize a live write — defense in
depth against a future caller that reaches a tool directly with forged flags.

The marker lives under a private (``_``-prefixed) context key, so it is stripped
from model-proposed args (`policy.model_boundary`) and from redacted run/audit
surfaces; it is never serialized to clients.
"""

from __future__ import annotations

import secrets
from typing import Any

# Regenerated every process start; never persisted or sent to clients.
_EXECUTION_APPROVAL_MARKER = secrets.token_hex(32)
EXECUTION_APPROVAL_CONTEXT_KEY = "_validated_execution_approval"


def mark_execution_approved(context: Any) -> None:
    """Stamp ``context`` as having passed a validated approval/claim gate."""
    if isinstance(context, dict):
        context[EXECUTION_APPROVAL_CONTEXT_KEY] = _EXECUTION_APPROVAL_MARKER


def execution_is_marked_approved(context: Any) -> bool:
    """Return True only when ``context`` carries the current process marker."""
    if not isinstance(context, dict):
        return False
    return secrets.compare_digest(
        str(context.get(EXECUTION_APPROVAL_CONTEXT_KEY) or ""),
        _EXECUTION_APPROVAL_MARKER,
    )
