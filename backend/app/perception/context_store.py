from __future__ import annotations

from app.perception.schemas import AppContext, PerceptionEvent, ScreenState

_latest_screen_state: ScreenState | None = None
_latest_app_context: AppContext | None = None


def update_screen_state(state: ScreenState) -> None:
    global _latest_screen_state, _latest_app_context
    _latest_screen_state = state
    if state.app_context is not None:
        _latest_app_context = state.app_context


def update_app_context(context: AppContext) -> None:
    global _latest_app_context
    _latest_app_context = context


def handle_perception_event(event: PerceptionEvent) -> None:
    update_screen_state(event.screen_state)


def latest_screen_state() -> ScreenState | None:
    return _latest_screen_state


def latest_app_context() -> AppContext | None:
    return _latest_app_context


def latest_perception_context() -> dict[str, object]:
    context: dict[str, object] = {}
    if _latest_screen_state is not None:
        context["screen_state"] = _latest_screen_state
    if _latest_app_context is not None:
        context["app_context"] = _latest_app_context
    return context


def clear() -> None:
    global _latest_screen_state, _latest_app_context
    _latest_screen_state = None
    _latest_app_context = None
