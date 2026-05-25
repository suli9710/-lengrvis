from __future__ import annotations

import base64
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.schemas import now_iso
from app.perception.app_context import get_current_app_context
from app.perception.context_store import handle_perception_event, update_screen_state
from app.perception.schemas import AppContext, PerceptionEvent, PerceptionProvider, ScreenState
from app.services.remote_desktop_service import (
    DEFAULT_CAPTURE_HEIGHT,
    DEFAULT_CAPTURE_WIDTH,
    DEFAULT_JPEG_QUALITY,
    capture_screen_frame,
)
from app.tools.vision_tools import describe_image


CaptureFn = Callable[..., Any]
DescribeFn = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
AppContextFn = Callable[[], AppContext]
EventPublisher = Callable[[PerceptionEvent], Any]


@dataclass(slots=True)
class ScreenMonitorConfig:
    enabled: bool = False
    interval_seconds: float = 5.0
    publish_events: bool = False
    task_id: str = ""
    max_width: int = DEFAULT_CAPTURE_WIDTH
    max_height: int = DEFAULT_CAPTURE_HEIGHT
    quality: int = DEFAULT_JPEG_QUALITY
    vision_context: dict[str, Any] = field(default_factory=dict)
    temp_dir: str | None = None

    @classmethod
    def from_settings(cls, settings: Any) -> "ScreenMonitorConfig":
        return cls(
            enabled=bool(getattr(settings, "perception_enabled", False)),
            interval_seconds=float(getattr(settings, "perception_interval_seconds", 5.0) or 5.0),
            publish_events=bool(getattr(settings, "perception_publish_events", False)),
            max_width=int(getattr(settings, "perception_max_width", DEFAULT_CAPTURE_WIDTH) or DEFAULT_CAPTURE_WIDTH),
            max_height=int(getattr(settings, "perception_max_height", DEFAULT_CAPTURE_HEIGHT) or DEFAULT_CAPTURE_HEIGHT),
            quality=int(getattr(settings, "perception_jpeg_quality", DEFAULT_JPEG_QUALITY) or DEFAULT_JPEG_QUALITY),
            vision_context={
                "settings": settings,
                "allowed_directories": list(getattr(settings, "allowed_directories", []) or []),
            },
        )


class ScreenMonitor(PerceptionProvider):
    def __init__(
        self,
        config: ScreenMonitorConfig | None = None,
        *,
        enabled: bool | None = None,
        interval_seconds: float | None = None,
        publish_events: bool | None = None,
        task_id: str | None = None,
        capture_fn: CaptureFn | None = None,
        describe_fn: DescribeFn | None = None,
        app_context_fn: AppContextFn | None = None,
        event_publisher: EventPublisher | Any | None = None,
    ) -> None:
        self.config = config or ScreenMonitorConfig()
        if enabled is not None:
            self.config.enabled = enabled
        if interval_seconds is not None:
            self.config.interval_seconds = interval_seconds
        if publish_events is not None:
            self.config.publish_events = publish_events
        if task_id is not None:
            self.config.task_id = task_id

        self.capture_fn = capture_fn or capture_screen_frame
        self.describe_fn = describe_fn or describe_image
        self.app_context_fn = app_context_fn or get_current_app_context
        self.event_publisher = event_publisher
        self.last_state: ScreenState | None = None
        self.last_error: str = ""
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def start(self) -> bool:
        if not self.enabled:
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ScreenMonitor", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def poll_once(self) -> ScreenState | None:
        if not self.enabled:
            return None
        return self.capture_once()

    def capture_screen_state(self) -> ScreenState:
        return self.capture_once()

    def capture_once(self) -> ScreenState:
        frame = self.capture_fn(
            max_width=self.config.max_width,
            max_height=self.config.max_height,
            quality=self.config.quality,
        )
        with _temporary_image(frame, self.config.temp_dir) as image_path:
            vision_result = self.describe_fn({"path": str(image_path)}, self._vision_context(image_path.parent))

        state = _state_from_frame(
            frame,
            vision_result=vision_result,
            app_context=self.app_context_fn(),
        )
        self.last_state = state
        self.last_error = ""
        update_screen_state(state)

        if self.config.publish_events:
            self._publish(state)
        return state

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            self._stop_event.wait(max(0.1, float(self.config.interval_seconds or 5.0)))

    def _vision_context(self, temp_dir: Path) -> dict[str, Any]:
        context = dict(self.config.vision_context or {})
        allowed = list(context.get("allowed_directories") or [])
        temp_dir_text = str(temp_dir)
        if temp_dir_text not in allowed:
            allowed.append(temp_dir_text)
        context["allowed_directories"] = allowed
        return context

    def _publish(self, state: ScreenState) -> None:
        if self.event_publisher is None:
            return
        event = PerceptionEvent(task_id=self.config.task_id, screen_state=state)
        handle_perception_event(event)
        if callable(self.event_publisher):
            self.event_publisher(event)
            return
        publish = getattr(self.event_publisher, "publish", None)
        if callable(publish):
            publish(event)


class _temporary_image:
    def __init__(self, frame: Any, temp_dir: str | None) -> None:
        self.frame = frame
        self.temp_dir = temp_dir
        self.path: Path | None = None

    def __enter__(self) -> Path:
        raw = _frame_image_base64(self.frame)
        data = base64.b64decode(raw)
        suffix = ".jpg"
        handle = tempfile.NamedTemporaryFile(suffix=suffix, dir=self.temp_dir, delete=False)
        try:
            handle.write(data)
            self.path = Path(handle.name)
            return self.path
        finally:
            handle.close()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.path is not None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def _state_from_frame(frame: Any, *, vision_result: dict[str, Any], app_context: AppContext) -> ScreenState:
    metadata = dict(vision_result.get("metadata") or {}) if isinstance(vision_result, dict) else {}
    ok = bool(vision_result.get("ok", True)) if isinstance(vision_result, dict) else False
    if not ok and isinstance(vision_result, dict) and vision_result.get("error"):
        metadata["vision_error"] = str(vision_result["error"])

    return ScreenState(
        captured_at=str(getattr(frame, "timestamp", "") or now_iso()),
        image_base64=_frame_image_base64(frame),
        width=int(getattr(frame, "width", 0) or 0),
        height=int(getattr(frame, "height", 0) or 0),
        original_width=int(getattr(frame, "original_width", 0) or 0),
        original_height=int(getattr(frame, "original_height", 0) or 0),
        description=str(vision_result.get("description", "") if isinstance(vision_result, dict) else ""),
        tags=list(vision_result.get("tags") or []) if isinstance(vision_result, dict) else [],
        structured_labels=dict(vision_result.get("structured_labels") or {}) if isinstance(vision_result, dict) else {},
        app_context=app_context,
        metadata=metadata,
    )


def _frame_image_base64(frame: Any) -> str:
    if isinstance(frame, str):
        return frame.removeprefix("data:image/jpeg;base64,")
    value = getattr(frame, "image_base64", "")
    return str(value or "")
