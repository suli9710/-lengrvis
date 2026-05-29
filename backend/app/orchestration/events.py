from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.core.schemas import new_id, now_iso


class StepEvent(StrEnum):
    TOOL_FAILED = "tool.failed"


class Event(BaseModel):
    """Base model for lightweight dispatcher events.

    The production task workflow is driven by direct handler method calls, not
    by dispatching orchestration event classes. This base remains shared with
    perception and environment stream events so they can use the local
    EventDispatcher fan-out, AgentBus notification, and audit path.
    """

    id: str = Field(default_factory=lambda: new_id("evt"))
    event_type: str
    task_id: str
    timestamp: str = Field(default_factory=now_iso)
    source_agent: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    def summary(self) -> str:
        return f"[{self.event_type}] task={self.task_id}"

    def _typed_fields(self) -> dict[str, Any]:
        """Return fields that are specific to the concrete subclass."""
        base_keys = set(Event.model_fields.keys())
        return {key: value for key, value in self.model_dump().items() if key not in base_keys}


class ToolFailed(Event):
    """Recovery emits this for notification and audit after a tool failure."""

    event_type: str = StepEvent.TOOL_FAILED.value
    step_id: str = ""
    tool_name: str = ""
    error: str = ""
    retry_count: int = 0

    def summary(self) -> str:
        return f"Tool {self.tool_name} failed for step {self.step_id}: {self.error[:80]}"


EVENT_REGISTRY: dict[str, type[Event]] = {
    StepEvent.TOOL_FAILED.value: ToolFailed,
}


def event_to_dict(event: Event) -> dict[str, Any]:
    """Convert an event to a dict compatible with AgentBus message format."""
    return {
        "id": event.id,
        "event_type": event.event_type,
        "task_id": event.task_id,
        "timestamp": event.timestamp,
        "source_agent": event.source_agent,
        "content": event.summary(),
        "structured_payload": event._typed_fields(),
    }


def event_from_dict(data: dict[str, Any]) -> Event:
    """Reconstruct an event from a dict produced by ``event_to_dict``.

    Only concrete event types with current production emitters are registered.
    Unknown event types fall back to the base ``Event``.
    """
    event_type = data.get("event_type", "")
    cls = EVENT_REGISTRY.get(event_type, Event)
    payload = data.get("structured_payload") or data.get("payload") or {}
    merged = {**data, **payload}
    return cls.model_validate(merged)
