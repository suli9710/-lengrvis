from __future__ import annotations

import pytest

from app.orchestration.events import (
    EVENT_REGISTRY,
    Event,
    ToolFailed,
    event_from_dict,
    event_to_dict,
)


def test_registry_only_contains_currently_emitted_orchestration_events():
    assert EVENT_REGISTRY == {"tool.failed": ToolFailed}


def test_registry_values_are_event_subclasses():
    for cls in EVENT_REGISTRY.values():
        assert issubclass(cls, Event)
        assert cls is not Event


def test_base_event_summary_and_defaults():
    event = Event(event_type="custom.observed", task_id="task_1")

    assert event.id.startswith("evt_")
    assert event.timestamp
    assert event.payload == {}
    assert event.summary() == "[custom.observed] task=task_1"


def test_tool_failed_fields_and_summary():
    event = ToolFailed(
        task_id="task_1",
        step_id="step_1",
        tool_name="file.write",
        error="disk full",
        retry_count=2,
    )

    assert event.event_type == "tool.failed"
    assert event.step_id == "step_1"
    assert event.tool_name == "file.write"
    assert event.retry_count == 2
    assert "disk full" in event.summary()


def test_event_to_dict_uses_typed_fields_as_structured_payload():
    event = ToolFailed(task_id="task_1", step_id="step_1", tool_name="file.write", error="boom")

    serialized = event_to_dict(event)

    assert serialized["event_type"] == "tool.failed"
    assert serialized["task_id"] == "task_1"
    assert serialized["content"] == event.summary()
    assert serialized["structured_payload"] == {
        "step_id": "step_1",
        "tool_name": "file.write",
        "error": "boom",
        "retry_count": 0,
    }


def test_event_from_dict_restores_registered_tool_failed_event():
    original = ToolFailed(task_id="task_1", step_id="step_1", tool_name="file.write", error="boom")

    restored = event_from_dict(event_to_dict(original))

    assert type(restored) is ToolFailed
    assert restored.task_id == "task_1"
    assert restored.step_id == "step_1"
    assert restored.tool_name == "file.write"
    assert restored.error == "boom"


@pytest.mark.parametrize(
    "event_type",
    [
        "task.created",
        "goal.reviewed",
        "plan.generated",
        "consultation.done",
        "plan.reviewed",
        "step.ready",
        "subagent.responded",
        "safety_review.done",
        "approval.needed",
        "approval.received",
        "tool.executed",
        "observation.recorded",
        "reflection.done",
        "all_steps.resolved",
        "task.finalized",
    ],
)
def test_retired_workflow_event_names_fall_back_to_base_event(event_type: str):
    restored = event_from_dict({"event_type": event_type, "task_id": "task_1", "payload": {"x": 1}})

    assert type(restored) is Event
    assert restored.event_type == event_type
    assert restored.payload == {"x": 1}
