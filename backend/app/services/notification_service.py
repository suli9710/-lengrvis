from __future__ import annotations

from app.core.audit import record
from app.core.schemas import MessageType
from app.orchestration.agent_bus import AgentBus

SYSTEM_TASK_ID = "__system__"

_bus: AgentBus | None = None


def _get_bus() -> AgentBus:
    global _bus
    if _bus is None:
        _bus = AgentBus()
    return _bus


def init_bus(bus: AgentBus) -> None:
    global _bus
    _bus = bus


def notify(
    body: str,
    title: str = "",
    *,
    task_id: str | None = None,
    severity: str = "info",
) -> dict:
    """Send a notification through the agent bus.

    Supports both the new signature ``notify(title, body)`` and the legacy
    ``notify(message)`` single-argument form for backward compatibility.
    When called with two positional strings the first is treated as *title*
    and the second as *body*.  When only one positional string is provided
    it is used as *body* (and *title* defaults to empty string).
    """
    # notify(title, body) arrives as body=<title>, title=<body> due to
    # parameter order.  Swap when both positional args are supplied so the
    # public contract is notify(title, body).  A single-arg call (legacy)
    # leaves title="" and body=<message>, which needs no correction.
    if title:
        body, title = title, body

    effective_task_id = task_id or SYSTEM_TASK_ID
    bus = _get_bus()
    bus.publish_text(
        effective_task_id,
        "NotificationService",
        body,
        message_type=MessageType.NOTIFICATION,
        structured_payload={
            "title": title,
            "body": body,
            "severity": severity,
        },
    )
    record(
        "notification.sent",
        "NotificationService",
        {"title": title, "severity": severity, "task_id": effective_task_id},
        task_id=effective_task_id,
    )
    return {"queued": True, "title": title, "body": body, "task_id": effective_task_id, "severity": severity}
