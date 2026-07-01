from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core import db
from app.policy.redaction import redact_audit_storage_payload

if TYPE_CHECKING:
    from app.core.schemas import AuditEvent


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_audit_storage_payload(payload)
    return redacted if isinstance(redacted, dict) else {}


def record(
    event_type: str, actor: str, payload: dict[str, Any] | None = None, task_id: str | None = None
) -> AuditEvent:
    from app.core.schemas import AuditEvent

    event = AuditEvent(
        task_id=task_id,
        event_type=event_type,
        actor=actor,
        payload=dict(payload or {}),
    )
    return AuditEvent.model_validate(db.insert_audit_event(event))


def verify_chain(limit: int | None = None) -> dict[str, Any]:
    from app.core.schemas import AuditChainVerification

    return AuditChainVerification.model_validate(db.verify_audit_log(limit=limit)).model_dump(mode="json")
