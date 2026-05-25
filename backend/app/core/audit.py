from __future__ import annotations

from typing import Any

from app.core import db
from app.core.schemas import AuditEvent


SENSITIVE_KEYS = {"api_key", "password", "token", "cookie", "authorization"}


def sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in SENSITIVE_KEYS:
            sanitized[key] = "***"
        elif isinstance(value, dict):
            sanitized[key] = sanitize_payload(value)
        else:
            sanitized[key] = value
    return sanitized


def record(event_type: str, actor: str, payload: dict[str, Any] | None = None, task_id: str | None = None) -> AuditEvent:
    event = AuditEvent(
        task_id=task_id,
        event_type=event_type,
        actor=actor,
        payload=sanitize_payload(payload or {}),
    )
    db.upsert_model("audit_events", event)
    return event

