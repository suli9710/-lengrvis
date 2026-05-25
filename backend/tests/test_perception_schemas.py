from __future__ import annotations

from abc import ABC

from app.orchestration.events import Event, event_to_dict
from app.perception.schemas import AppContext, PerceptionEvent, PerceptionProvider, Rect, ScreenState, UIElement


def test_screen_state_serializes_nested_context():
    state = ScreenState(
        description="A browser window is visible.",
        width=1280,
        height=720,
        tags=["screenshot"],
        app_context=AppContext(
            platform="win32",
            available=True,
            active_window_title="Example",
            active_window_rect=Rect(x=1, y=2, width=300, height=400),
            focus_control=UIElement(role="button", name="Save"),
        ),
    )

    data = state.model_dump()

    assert data["description"] == "A browser window is visible."
    assert data["app_context"]["active_window_title"] == "Example"
    assert data["app_context"]["focus_control"]["name"] == "Save"


def test_perception_event_is_event_compatible():
    state = ScreenState(description="Settings panel", app_context=AppContext(active_window_title="Settings"))
    event = PerceptionEvent(task_id="task_1", screen_state=state)

    assert isinstance(event, Event)
    assert event.event_type == "perception.screen_state"
    assert event.summary() == "Screen state observed: Settings"

    serialized = event_to_dict(event)
    assert serialized["task_id"] == "task_1"
    assert serialized["structured_payload"]["screen_state"]["description"] == "Settings panel"


def test_perception_provider_is_abstract_contract():
    assert issubclass(PerceptionProvider, ABC)
    assert "capture_screen_state" in PerceptionProvider.__abstractmethods__
