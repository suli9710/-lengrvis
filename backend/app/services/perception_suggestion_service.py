from __future__ import annotations

import threading
from typing import Any

from app.core import db
from app.core.schemas import Run, RunEngine
from app.core.session_context import get_session_context_store
from app.llm.registry import get_effective_settings
from app.perception.context_store import latest_app_context, latest_screen_state, update_screen_state
from app.perception.intent_predictor import IntentSuggestion, predict_intents
from app.perception.schemas import AppContext, PerceptionEvent, ScreenState
from app.perception.screen_monitor import ScreenMonitor, ScreenMonitorConfig
from app.perception.storage import app_context_summary, is_sensitive_context, screen_state_summary
from app.services import run_service


_SUGGESTIONS: dict[str, IntentSuggestion] = {}
_LOCK = threading.RLock()


def current_state() -> dict[str, Any]:
    screen_state = latest_screen_state()
    app_context = latest_app_context()
    sensitive = is_sensitive_context(screen_state=screen_state, app_context=app_context)
    return {
        "available": screen_state is not None or app_context is not None,
        "sensitive_context_suppressed": sensitive,
        "screen_state": screen_state_summary(screen_state) if screen_state is not None else None,
        "app_context": app_context_summary(app_context) if app_context is not None else None,
    }


def current_suggestions() -> list[IntentSuggestion]:
    screen_state = latest_screen_state()
    app_context = latest_app_context()
    if is_sensitive_context(screen_state=screen_state, app_context=app_context):
        with _LOCK:
            _SUGGESTIONS.clear()
        return []
    history = get_session_context_store().current
    suggestions = predict_intents(screen_state=screen_state, app_context=app_context, history=history)
    with _LOCK:
        _SUGGESTIONS.clear()
        _SUGGESTIONS.update({item.id: item for item in suggestions})
    _store_suggestions(suggestions)
    return suggestions


def get_suggestion(suggestion_id: str) -> IntentSuggestion | None:
    with _LOCK:
        suggestion = _SUGGESTIONS.get(suggestion_id)
    if suggestion is not None:
        return suggestion
    for item in current_suggestions():
        if item.id == suggestion_id:
            return item
    return None


def capture_once() -> ScreenState:
    settings = get_effective_settings()
    config = ScreenMonitorConfig.from_settings(settings)
    config.enabled = True
    config.publish_events = False
    monitor = ScreenMonitor(config)
    state = monitor.capture_once()
    update_screen_state(state)
    return state


def capture_once_summary() -> dict[str, Any]:
    return screen_state_summary(capture_once()) or {"available": False}


async def launch_suggestion(
    suggestion_id: str,
    *,
    mode: str = "efficiency",
    engine: RunEngine = RunEngine.AUTO,
) -> Run:
    suggestion = get_suggestion(suggestion_id)
    if suggestion is None:
        raise KeyError(suggestion_id)
    run = await run_service.create_run(
        _launch_prompt(suggestion),
        mode,
        engine,
        agent_hint=suggestion.agent_hint,
    )
    _mark_suggestion_launched(suggestion, run)
    return run


def perception_event_from_state(state: ScreenState, task_id: str = "") -> PerceptionEvent:
    return PerceptionEvent(task_id=task_id, screen_state=state)


def _launch_prompt(suggestion: IntentSuggestion) -> str:
    parts = [suggestion.prompt.strip()]
    context = app_context_summary(latest_app_context())
    if context:
        parts.append(f"Current visible app context: {context}")
    if suggestion.reason:
        parts.append(f"Why this was suggested: {suggestion.reason}")
    return "\n\n".join(part for part in parts if part)


def _store_suggestions(suggestions: list[IntentSuggestion]) -> None:
    for suggestion in suggestions:
        payload = {
            "id": suggestion.id,
            "task_id": "",
            "suggestion_id": suggestion.id,
            "rule_id": suggestion.metadata.get("rule_id", ""),
            "severity": "info",
            "title": suggestion.title,
            "summary": suggestion.reason or suggestion.prompt,
            "prompt": suggestion.prompt,
            "reason": suggestion.reason,
            "confidence": suggestion.confidence,
            "agent_hint": suggestion.agent_hint,
            "status": suggestion.metadata.get("status", "proposed"),
            "linked_run_id": suggestion.metadata.get("linked_run_id", ""),
            "expires_at": suggestion.metadata.get("expires_at", ""),
            "payload": suggestion.model_dump(mode="json"),
        }
        try:
            db.insert_perception_suggestion(payload)
        except Exception:
            continue


def _mark_suggestion_launched(suggestion: IntentSuggestion, run: Run) -> None:
    payload = {
        "id": suggestion.id,
        "task_id": run.task_id or "",
        "suggestion_id": suggestion.id,
        "rule_id": suggestion.metadata.get("rule_id", ""),
        "severity": "info",
        "title": suggestion.title,
        "summary": suggestion.reason or suggestion.prompt,
        "prompt": suggestion.prompt,
        "reason": suggestion.reason,
        "confidence": suggestion.confidence,
        "agent_hint": suggestion.agent_hint,
        "status": "launched",
        "linked_run_id": run.id,
        "expires_at": suggestion.metadata.get("expires_at", ""),
        "payload": {**suggestion.model_dump(mode="json"), "linked_run_id": run.id, "status": "launched"},
    }
    try:
        db.insert_perception_suggestion(payload)
    except Exception:
        return
