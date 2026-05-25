from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from app.core.schemas import new_id, now_iso
from app.orchestration.events import Event


class Rect(BaseModel):
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0


class UIElement(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ui"))
    role: str = ""
    name: str = ""
    text: str = ""
    bounding_box: Rect | None = None
    confidence: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class AppContext(BaseModel):
    platform: str = ""
    available: bool = False
    active_window_title: str = ""
    process_name: str = ""
    process_id: int | None = None
    active_window_rect: Rect | None = None
    focus_control: UIElement | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ScreenState(BaseModel):
    id: str = Field(default_factory=lambda: new_id("screen"))
    captured_at: str = Field(default_factory=now_iso)
    description: str = ""
    width: int = 0
    height: int = 0
    original_width: int = 0
    original_height: int = 0
    image_base64: str = ""
    mime_type: str = "image/jpeg"
    tags: list[str] = Field(default_factory=list)
    structured_labels: dict[str, Any] = Field(default_factory=dict)
    ui_elements: list[UIElement] = Field(default_factory=list)
    app_context: AppContext | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PerceptionProvider(ABC):
    @abstractmethod
    def capture_screen_state(self) -> ScreenState:
        """Capture and describe the current user-visible state."""


class PerceptionEvent(Event):
    event_type: str = "perception.screen_state"
    task_id: str = ""
    source_agent: str = "ScreenMonitor"
    screen_state: ScreenState

    def summary(self) -> str:
        title = ""
        if self.screen_state.app_context is not None:
            title = self.screen_state.app_context.active_window_title
        description = self.screen_state.description[:80]
        detail = title or description or self.screen_state.id
        return f"Screen state observed: {detail}"
