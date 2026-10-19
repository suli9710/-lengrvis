from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

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
        seen["image_path"] = path
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
    assert seen["image_path"].exists() is False
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


def test_screen_monitor_reuses_vision_result_for_unchanged_frame(tmp_path):
    calls = {"describe": 0}

    def describe(args, context):
        calls["describe"] += 1
        path = tmp_path.__class__(args["path"])
        assert path.exists()
        return {
            "ok": True,
            "description": f"Desktop pass {calls['describe']}",
            "tags": ["screenshot"],
            "metadata": {"source": "test"},
        }

    monitor = ScreenMonitor(
        ScreenMonitorConfig(enabled=True, temp_dir=str(tmp_path)),
        capture_fn=lambda **kwargs: _frame(),
        describe_fn=describe,
        app_context_fn=lambda: AppContext(active_window_title="Desktop"),
    )

    first = monitor.poll_once()
    second = monitor.poll_once()

    assert calls["describe"] == 1
    assert first is not None
    assert second is not None
    assert second.description == first.description
    assert second.metadata["source"] == "test"
    assert second.metadata["frame_diff_gate"] == "reused"


def test_screen_monitor_rejects_oversized_base64_before_tempfile(monkeypatch, tmp_path):
    import app.perception.screen_monitor as screen_monitor

    monkeypatch.setattr(screen_monitor, "MAX_SCREEN_IMAGE_BYTES", 4)
    monkeypatch.setattr(screen_monitor, "MAX_SCREEN_IMAGE_BASE64_CHARS", 8)
    frame = SimpleNamespace(
        image_base64=base64.b64encode(b"too-large").decode("ascii"),
        timestamp="2026-05-26T00:00:00+00:00",
        width=640,
        height=360,
        original_width=1280,
        original_height=720,
        quality=50,
    )
    monitor = ScreenMonitor(
        ScreenMonitorConfig(enabled=True, temp_dir=str(tmp_path)),
        capture_fn=lambda **kwargs: frame,
        describe_fn=lambda args, context: {"ok": True, "description": "Desktop"},
        app_context_fn=lambda: AppContext(active_window_title="Desktop"),
    )

    with pytest.raises(ValueError, match="screen image exceeds"):
        monitor.poll_once()

    assert list(tmp_path.iterdir()) == []


def test_global_monitor_uses_current_privacy_settings_before_every_vision_call(monkeypatch, tmp_path):
    from app.config import AppSettings
    from app.llm import registry
    from app.perception import screen_monitor
    from app.tools import vision_tools

    startup = AppSettings(
        mode="efficiency",
        provider_name="openai",
        api_key="test-cloud-key",
        perception_enabled=True,
        perception_frame_diff_gate_enabled=False,
        allowed_directories=["startup-directory"],
    )
    privacy = startup.model_copy(update={"mode": "privacy", "allowed_directories": ["current-directory"]})
    current = {"settings": startup}
    selected = []
    cloud_calls = []
    vision_calls = []

    class LocalVisionProvider:
        name = "ollama"

        def __init__(self, settings):
            self.config = settings.model_copy(
                update={"provider_name": "ollama", "base_url": "http://127.0.0.1:11434/v1"}
            )

        async def vision(self, image_path, prompt, model=None):
            vision_calls.append(image_path)
            return "A local desktop description."

    def local_provider(settings):
        selected.append(settings)
        return LocalVisionProvider(settings)

    def cloud_provider(settings):
        cloud_calls.append(settings.mode)
        return LocalVisionProvider(settings)

    monkeypatch.setattr(screen_monitor, "get_effective_settings", lambda: current["settings"])
    monkeypatch.setattr(registry, "get_effective_settings", lambda: current["settings"])
    monkeypatch.setattr(registry, "_build_local_provider", local_provider)
    monkeypatch.setattr(registry, "_build_cloud_provider", cloud_provider)
    monkeypatch.setattr(vision_tools, "extract_image_metadata", lambda path: {})
    config = ScreenMonitorConfig.from_settings(startup)
    config.temp_dir = str(tmp_path)
    contexts = []

    def describe(args, context):
        contexts.append(context)
        return vision_tools.describe_image(args, context)

    monitor = ScreenMonitor(
        config,
        capture_fn=lambda **kwargs: _frame(),
        describe_fn=describe,
        app_context_fn=lambda: AppContext(active_window_title="Desktop"),
    )
    current["settings"] = privacy
    assert monitor.poll_once().description == "A local desktop description."
    latest = privacy.model_copy(update={"allowed_directories": ["latest-directory"]})
    current["settings"] = latest
    assert monitor.poll_once().description == "A local desktop description."

    assert selected == [privacy, latest]
    assert len(vision_calls) == 2
    assert cloud_calls == []
    assert contexts[0]["settings"] is privacy
    assert contexts[1]["settings"] is latest
    assert contexts[0]["allowed_directories"] == ["current-directory", str(tmp_path)]
    assert contexts[1]["allowed_directories"] == ["latest-directory", str(tmp_path)]
    assert config.vision_context["settings"] is startup
    assert list(tmp_path.glob("*.jpg")) == []


def test_explicit_task_monitor_keeps_its_settings_snapshot(monkeypatch, tmp_path):
    from app.config import AppSettings
    from app.perception import screen_monitor

    task_settings = AppSettings(mode="privacy", allowed_directories=["task-directory"])
    contexts = []

    def global_settings():
        raise AssertionError("An explicit task monitor must keep its supplied settings snapshot")

    monkeypatch.setattr(screen_monitor, "get_effective_settings", global_settings)

    def describe(args, context):
        contexts.append(context)
        return {"ok": True, "description": "Local task capture"}

    monitor = ScreenMonitor(
        ScreenMonitorConfig(
            enabled=True,
            task_id="private-task",
            temp_dir=str(tmp_path),
            vision_context={"settings": task_settings, "allowed_directories": ["task-directory"]},
        ),
        capture_fn=lambda **kwargs: _frame(),
        describe_fn=describe,
        app_context_fn=lambda: AppContext(active_window_title="Desktop"),
    )
    assert monitor.poll_once().description == "Local task capture"
    assert contexts[0]["settings"] is task_settings
    assert contexts[0]["allowed_directories"] == ["task-directory", str(tmp_path)]


def test_global_settings_failure_does_not_use_the_startup_vision_context(monkeypatch, tmp_path):
    from app.config import AppSettings
    from app.perception import screen_monitor

    def unavailable_settings():
        raise RuntimeError("Current settings unavailable")

    monkeypatch.setattr(screen_monitor, "get_effective_settings", unavailable_settings)
    described = []
    config = ScreenMonitorConfig.from_settings(AppSettings(mode="efficiency", perception_enabled=True))
    config.temp_dir = str(tmp_path)
    monitor = ScreenMonitor(
        config,
        capture_fn=lambda **kwargs: _frame(),
        describe_fn=lambda args, context: described.append(context),
        app_context_fn=lambda: AppContext(active_window_title="Desktop"),
    )
    with pytest.raises(RuntimeError, match="Current settings unavailable"):
        monitor.poll_once()
    assert described == []
    assert list(tmp_path.iterdir()) == []
