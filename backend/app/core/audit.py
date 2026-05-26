from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core import db
from app.policy.redaction import redact_value

if TYPE_CHECKING:
    from app.core.schemas import AuditEvent


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_value(payload)
    return redacted if isinstance(redacted, dict) else {}


def record(event_type: str, actor: str, payload: dict[str, Any] | None = None, task_id: str | None = None) -> "AuditEvent":
    from app.core.schemas import AuditEvent

    event = AuditEvent(
        task_id=task_id,
        event_type=event_type,
        actor=actor,
        payload=sanitize_payload(payload or {}),
    )
    db.upsert_model("audit_events", event)
    return event
