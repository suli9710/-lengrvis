from __future__ import annotations

import base64
from types import SimpleNamespace

from app.perception.schemas import AppContext, PerceptionEvent
from app.perception.screen_monitor import ScreenMonitor, ScreenMonitorConfig


def _frame() -> SimpleNamespace:
    return SimpleNamespace(
        image_base64=base64.b64encode(b"fake-jpeg").decode("ascii"),
        timestamp="2026-05-26T00:00:00+00:00",
        width=640,
        height=360,
        original_width=1280,
        original_height=720,
        quality=50,
    )


def test_screen_monitor_disabled_by_default():
    calls = {"capture": 0}

    def capture(**kwargs):
        calls["capture"] += 1
        return _frame()

    monitor = ScreenMonitor(capture_fn=capture)

    assert monitor.enabled is False
    assert monitor.poll_once() is None
    assert monitor.start() is False
    assert calls["capture"] == 0


def test_capture_once_describes_frame_and_builds_state(tmp_path):
    seen = {}

    def capture(**kwargs):
        seen["capture_kwargs"] = kwargs
        return _frame()

    def describe(args, context):
        path = tmp_path.__class__(args["path"])
        seen["image_exists"] = path.exists()
        seen["allowed_directories"] = context["allowed_directories"]
        return {
            "ok": True,
            "description": "A document editor with a toolbar.",
            "tags": ["screenshot", "document"],
            "structured_labels": {"scene_type": "screenshot"},
            "metadata": {"source": "test"},
        }

    monitor = ScreenMonitor(
        ScreenMonitorConfig(enabled=True, temp_dir=str(tmp_path)),
        capture_fn=capture,
        describe_fn=describe,
        app_context_fn=lambda: AppContext(platform="win32", available=True, active_window_title="Doc"),
    )

    state = monitor.poll_once()

    assert state is not None
    assert state.description == "A document editor with a toolbar."
    assert state.width == 640
    assert state.original_height == 720
    assert state.tags == ["screenshot", "document"]
    assert state.structured_labels["scene_type"] == "screenshot"
    assert state.metadata["source"] == "test"
    assert state.app_context.active_window_title == "Doc"
    assert seen["image_exists"] is True
    assert str(tmp_path) in seen["allowed_directories"]
    assert monitor.last_state is state


def test_screen_monitor_optionally_publishes_perception_event():
    events = []
    monitor = ScreenMonitor(
        ScreenMonitorConfig(enabled=True, publish_events=True, task_id="task_1"),
        capture_fn=lambda **kwargs: _frame(),
        describe_fn=lambda args, context: {"ok": True, "description": "Desktop"},
        app_context_fn=lambda: AppContext(active_window_title="Desktop"),
        event_publisher=events.append,
    )

    state = monitor.poll_once()

    assert state.description == "Desktop"
    assert len(events) == 1
    assert isinstance(events[0], PerceptionEvent)
    assert events[0].task_id == "task_1"
    assert events[0].screen_state.id == state.id
