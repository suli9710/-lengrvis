from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.session_context import SessionContext
from app.perception.schemas import AppContext, ScreenState, UIElement


CONFIDENCE_THRESHOLD = 0.8
MAX_SUGGESTIONS = 3


class IntentSuggestion(BaseModel):
    id: str
    title: str
    prompt: str
    confidence: float
    agent_hint: str = ""
    reason: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class IntentModel(Protocol):
    def predict(self, features: dict[str, Any]) -> Iterable[IntentSuggestion | Mapping[str, Any]]:
        """Return candidate intent suggestions for the visible local context."""


@dataclass(slots=True)
class IntentPredictor:
    model: IntentModel | None = None
    confidence_threshold: float = CONFIDENCE_THRESHOLD
    max_suggestions: int = MAX_SUGGESTIONS

    def predict(
        self,
        *,
        screen_state: ScreenState | None = None,
        app_context: AppContext | None = None,
        history: Sequence[Any] | SessionContext | Mapping[str, Any] | None = None,
    ) -> list[IntentSuggestion]:
        context = _resolve_app_context(screen_state, app_context)
        features = build_intent_features(screen_state=screen_state, app_context=context, history=history)
        candidates = self._model_candidates(features) + heuristic_candidates(features)
        return rank_suggestions(candidates, confidence_threshold=self.confidence_threshold, limit=self.max_suggestions)

    def _model_candidates(self, features: dict[str, Any]) -> list[IntentSuggestion]:
        if self.model is None:
            return []
        try:
            raw_candidates = self.model.predict(features)
        except Exception:
            return []
        return [_coerce_suggestion(item) for item in raw_candidates]


def predict_intents(
    *,
    screen_state: ScreenState | None = None,
    app_context: AppContext | None = None,
    history: Sequence[Any] | SessionContext | Mapping[str, Any] | None = None,
    model: IntentModel | None = None,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> list[IntentSuggestion]:
    return IntentPredictor(model=model, confidence_threshold=confidence_threshold).predict(
        screen_state=screen_state,
        app_context=app_context,
        history=history,
    )


def build_intent_features(
    *,
    screen_state: ScreenState | None,
    app_context: AppContext | None,
    history: Sequence[Any] | SessionContext | Mapping[str, Any] | None,
) -> dict[str, Any]:
    elements = list(screen_state.ui_elements if screen_state is not None else [])
    return {
        "screen_description": (screen_state.description if screen_state is not None else "").strip(),
        "screen_tags": list(screen_state.tags if screen_state is not None else []),
        "structured_labels": dict(screen_state.structured_labels if screen_state is not None else {}),
        "ui_text": _ui_text(elements),
        "ui_roles": sorted({item.role.lower() for item in elements if item.role}),
        "active_window_title": (app_context.active_window_title if app_context is not None else "").strip(),
        "process_name": (app_context.process_name if app_context is not None else "").strip(),
        "focus_text": _element_text(app_context.focus_control if app_context is not None else None),
        "history_text": _history_text(history),
        "unfinished_task_count": _unfinished_task_count(history),
        "learned_preferences": _learned_preferences(history),
    }


def heuristic_candidates(features: dict[str, Any]) -> list[IntentSuggestion]:
    text = _feature_text(features)
    candidates: list[IntentSuggestion] = []

    if _has_any(text, ("excel", "spreadsheet", "budget", ".xlsx", ".xls", "csv")):
        candidates.append(
            IntentSuggestion(
                id="spreadsheet_analyze",
                title="Analyze spreadsheet",
                prompt="Analyze the visible spreadsheet and summarize the important numbers.",
                confidence=_score(text, base=0.82, boosts=("budget", "chart", "pivot", "formula", "table")),
                agent_hint="DocumentAgent",
                reason="Spreadsheet context is visible.",
            )
        )

    if _has_any(text, ("word", "docx", "document", "report", "pdf", "proposal")):
        candidates.append(
            IntentSuggestion(
                id="document_summarize",
                title="Summarize document",
                prompt="Summarize the visible document and call out likely next actions.",
                confidence=_score(text, base=0.82, boosts=("report", "contract", "proposal", "review", "edit")),
                agent_hint="DocumentAgent",
                reason="Document editing or reading context is visible.",
            )
        )

    if _has_any(text, ("browser", "chrome", "edge", "firefox", "http", "web page", "网页", "页面")):
        candidates.append(
            IntentSuggestion(
                id="browser_extract",
                title="Read page",
                prompt="Read the current page and extract the useful facts.",
                confidence=_score(text, base=0.81, boosts=("search", "results", "article", "login", "checkout")),
                agent_hint="BrowserAgent",
                reason="Browser context is visible.",
            )
        )

    if _has_any(text, ("explorer", "folder", "directory", "downloads", "desktop", ".txt", ".png", ".pdf")):
        candidates.append(
            IntentSuggestion(
                id="file_organize",
                title="Organize files",
                prompt="Review the visible folder and suggest a safe organization plan.",
                confidence=_score(text, base=0.81, boosts=("downloads", "duplicate", "invoice", "contract")),
                agent_hint="FileAgent",
                reason="File or folder context is visible.",
            )
        )

    if _has_any(text, ("settings", "control panel", "task manager", "cpu", "memory", "disk", "network")):
        candidates.append(
            IntentSuggestion(
                id="system_diagnose",
                title="Check system",
                prompt="Check the visible system state and suggest a read-only diagnostic next step.",
                confidence=_score(text, base=0.81, boosts=("error", "warning", "slow", "low", "offline")),
                agent_hint="ComputerAgent",
                reason="System management context is visible.",
            )
        )

    if int(features.get("unfinished_task_count") or 0) > 0:
        candidates.append(
            IntentSuggestion(
                id="resume_task",
                title="Resume task",
                prompt="Resume the most recent unfinished task using the current screen context.",
                confidence=0.83,
                agent_hint="OrchestratorAgent",
                reason="Session history has unfinished tasks.",
            )
        )

    return candidates


def rank_suggestions(
    suggestions: Iterable[IntentSuggestion],
    *,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
    limit: int = MAX_SUGGESTIONS,
) -> list[IntentSuggestion]:
    by_id: dict[str, IntentSuggestion] = {}
    for suggestion in suggestions:
        if suggestion.confidence <= confidence_threshold:
            continue
        existing = by_id.get(suggestion.id)
        if existing is None or suggestion.confidence > existing.confidence:
            by_id[suggestion.id] = suggestion
    ranked = sorted(by_id.values(), key=lambda item: (-item.confidence, item.title))
    return ranked[: max(1, limit)]


def _resolve_app_context(screen_state: ScreenState | None, app_context: AppContext | None) -> AppContext | None:
    return app_context or (screen_state.app_context if screen_state is not None else None)


def _coerce_suggestion(raw: IntentSuggestion | Mapping[str, Any]) -> IntentSuggestion:
    if isinstance(raw, IntentSuggestion):
        return raw
    return IntentSuggestion.model_validate(dict(raw))


def _feature_text(features: dict[str, Any]) -> str:
    parts = [
        features.get("screen_description"),
        " ".join(features.get("screen_tags") or []),
        " ".join(str(value) for value in (features.get("structured_labels") or {}).values()),
        features.get("ui_text"),
        features.get("active_window_title"),
        features.get("process_name"),
        features.get("focus_text"),
        features.get("history_text"),
    ]
    return " ".join(str(part or "") for part in parts).lower()


def _has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term.lower() in text for term in terms)


def _score(text: str, *, base: float, boosts: Iterable[str]) -> float:
    matched = sum(1 for term in boosts if term.lower() in text)
    return min(0.97, base + matched * 0.04)


def _element_text(element: UIElement | None) -> str:
    if element is None:
        return ""
    return " ".join(part for part in [element.name, element.text, element.role] if part).strip()


def _ui_text(elements: Sequence[UIElement]) -> str:
    return " ".join(_element_text(item) for item in elements if _element_text(item))[:1000]


def _history_text(history: Sequence[Any] | SessionContext | Mapping[str, Any] | None) -> str:
    if history is None:
        return ""
    if isinstance(history, SessionContext):
        parts = list(history.notes[-5:])
        parts.extend(str(item) for item in history.unfinished_task_ids[-3:])
        parts.extend(str(value) for value in history.learned_preferences.values())
        return " ".join(parts)
    if isinstance(history, Mapping):
        return " ".join(str(value) for value in history.values() if value is not None)[:1000]
    return " ".join(str(item) for item in list(history)[-5:])[:1000]


def _unfinished_task_count(history: Sequence[Any] | SessionContext | Mapping[str, Any] | None) -> int:
    if isinstance(history, SessionContext):
        return len(history.unfinished_task_ids)
    if isinstance(history, Mapping):
        value = history.get("unfinished_task_ids")
        return len(value) if isinstance(value, Sequence) and not isinstance(value, str) else 0
    return 0


def _learned_preferences(history: Sequence[Any] | SessionContext | Mapping[str, Any] | None) -> dict[str, Any]:
    if isinstance(history, SessionContext):
        return dict(history.learned_preferences)
    if isinstance(history, Mapping):
        value = history.get("learned_preferences")
        return dict(value) if isinstance(value, Mapping) else {}
    return {}
