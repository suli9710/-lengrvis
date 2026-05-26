from __future__ import annotations

from app.agents.supervisor_agent import SupervisorAgent
from app.core.session_context import SessionContext
from app.perception.intent_predictor import IntentPredictor, IntentSuggestion, build_intent_features, predict_intents
from app.perception.schemas import AppContext, ScreenState, UIElement


class StubModel:
    def __init__(self) -> None:
        self.features = None

    def predict(self, features):
        self.features = features
        return [
            {
                "id": "model_visible_table",
                "title": "Model table action",
                "prompt": "Create a summary from the visible table.",
                "confidence": 0.91,
                "agent_hint": "DocumentAgent",
            },
            {
                "id": "below_threshold",
                "title": "Weak action",
                "prompt": "Weak action",
                "confidence": 0.8,
            },
        ]


def test_predictor_uses_injected_model_and_filters_threshold():
    model = StubModel()
    state = ScreenState(description="A table is visible", ui_elements=[UIElement(role="table", text="Revenue")])

    suggestions = IntentPredictor(model=model).predict(screen_state=state)

    assert [item.id for item in suggestions] == ["model_visible_table"]
    assert model.features["screen_description"] == "A table is visible"
    assert "Revenue" in model.features["ui_text"]


def test_heuristic_predicts_spreadsheet_intent_from_screen_and_app_context():
    state = ScreenState(
        description="Budget spreadsheet with chart and formulas",
        tags=["spreadsheet"],
        app_context=AppContext(available=True, process_name="EXCEL.EXE", active_window_title="Budget.xlsx"),
    )

    suggestions = predict_intents(screen_state=state)

    assert 1 <= len(suggestions) <= 3
    assert suggestions[0].id == "spreadsheet_analyze"
    assert suggestions[0].confidence > 0.8
    assert suggestions[0].agent_hint == "DocumentAgent"


def test_predictor_limits_to_three_ranked_suggestions():
    state = ScreenState(
        description="Chrome browser shows a PDF report in Downloads beside Task Manager memory warning",
        tags=["browser", "document"],
        app_context=AppContext(process_name="chrome.exe", active_window_title="report.pdf"),
    )
    history = SessionContext(unfinished_task_ids=["task_1"])

    suggestions = predict_intents(screen_state=state, history=history)

    assert len(suggestions) == 3
    assert all(item.confidence > 0.8 for item in suggestions)
    assert suggestions == sorted(suggestions, key=lambda item: (-item.confidence, item.title))


def test_build_features_accepts_session_history():
    history = SessionContext(
        unfinished_task_ids=["task_1", "task_2"],
        learned_preferences={"format": "concise"},
        notes=["User likes summaries"],
    )

    features = build_intent_features(screen_state=None, app_context=AppContext(process_name="notepad.exe"), history=history)

    assert features["unfinished_task_count"] == 2
    assert features["learned_preferences"] == {"format": "concise"}
    assert "User likes summaries" in features["history_text"]


def test_supervisor_proactive_suggestions_accepts_injected_predictor():
    class Predictor:
        def predict(self, **kwargs):
            return [
                IntentSuggestion(
                    id="resume_task",
                    title="Resume task",
                    prompt="Resume the task.",
                    confidence=0.9,
                    agent_hint="OrchestratorAgent",
                )
            ]

    suggestions = SupervisorAgent().proactive_suggestions(
        screen_state=ScreenState(description="Settings"),
        app_context=AppContext(active_window_title="Settings"),
        history={"unfinished_task_ids": ["task_1"]},
        predictor=Predictor(),
    )

    assert suggestions[0].prompt == "Resume the task."
    assert "Resume the task." in SupervisorAgent().proactive_reply(suggestions)
