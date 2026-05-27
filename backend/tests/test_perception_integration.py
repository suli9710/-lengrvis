from __future__ import annotations

import pytest

from app.agents.planner_agent import PlannerAgent
from app.agents.supervisor_agent import SupervisorAgent
from app.core.schemas import Plan, Task
from app.orchestration.dispatcher import EventDispatcher
from app.orchestration.handlers.planning_handler import PlanningHandler
from app.perception.context_store import clear, latest_perception_context, update_screen_state
from app.services import perception_suggestion_service
from app.perception.schemas import AppContext, PerceptionEvent, ScreenState, UIElement


@pytest.fixture(autouse=True)
def _clear_context_store():
    clear()
    yield
    clear()


@pytest.mark.asyncio
async def test_dispatcher_updates_latest_perception_context():
    dispatcher = EventDispatcher()
    from app.perception.context_store import handle_perception_event

    dispatcher.register("perception.screen_state", handle_perception_event)
    state = ScreenState(
        description="A spreadsheet is open.",
        app_context=AppContext(available=True, active_window_title="Budget.xlsx", process_name="EXCEL.EXE"),
    )

    await dispatcher.dispatch(PerceptionEvent(task_id="task_1", screen_state=state))

    context = latest_perception_context()
    assert context["screen_state"] is state
    assert context["app_context"].active_window_title == "Budget.xlsx"


def test_planner_formats_perception_context_for_prompt():
    planner = PlannerAgent()
    block = planner._format_perception_context(
        {
            "screen_state": ScreenState(description="A document editor is visible.", tags=["document"]),
            "app_context": AppContext(active_window_title="Report.docx", process_name="WINWORD.EXE"),
        }
    )

    assert "Current perception context" in block
    assert "A document editor is visible" in block
    assert "WINWORD.EXE" in block


def test_supervisor_formats_perception_context_for_prompt():
    supervisor = SupervisorAgent()
    block = supervisor._format_perception_context(
        {
            "screen_state": ScreenState(description="Settings window"),
            "app_context": AppContext(active_window_title="Settings", process_name="SystemSettings.exe"),
        }
    )

    assert "[Perception context]" in block
    assert "SystemSettings.exe" in block
    assert "Settings window" in block


def test_sensitive_window_does_not_generate_proactive_suggestions():
    update_screen_state(
        ScreenState(
            description="A browser login page is visible with password fields.",
            tags=["browser", "login"],
            app_context=AppContext(
                available=True,
                process_name="chrome.exe",
                active_window_title="Bank checkout - password required",
            ),
        )
    )

    suggestions = perception_suggestion_service.current_suggestions()

    assert suggestions == []


def test_sensitive_window_is_not_injected_into_planner_or_supervisor_prompts():
    context = {
        "screen_state": ScreenState(
            description="A login page is visible with one-time passcode text.",
            tags=["browser", "login"],
            app_context=AppContext(
                available=True,
                process_name="chrome.exe",
                active_window_title="Bank checkout - OTP",
            ),
        )
    }

    assert PlannerAgent()._format_perception_context(context) == ""
    assert SupervisorAgent()._format_perception_context(context) == ""


def test_sensitive_focused_control_does_not_generate_or_inject_context():
    state = ScreenState(
        description="A browser form is visible.",
        tags=["browser"],
        app_context=AppContext(
            available=True,
            process_name="chrome.exe",
            active_window_title="Chrome",
            focus_control=UIElement(role="edit", name="Password"),
        ),
    )
    update_screen_state(state)

    assert perception_suggestion_service.current_suggestions() == []
    context = {"screen_state": state, "app_context": state.app_context}
    assert PlannerAgent()._format_perception_context(context) == ""
    assert SupervisorAgent()._format_perception_context(context) == ""


def test_sensitive_ui_element_does_not_generate_suggestions():
    safe_elements = [UIElement(role="text", name=f"field-{index}") for index in range(12)]
    state = ScreenState(
        description="A browser page is visible.",
        tags=["browser"],
        app_context=AppContext(
            available=True,
            process_name="chrome.exe",
            active_window_title="Chrome",
        ),
        ui_elements=[*safe_elements, UIElement(role="textbox", name="token", text="API token")],
    )
    update_screen_state(state)

    assert perception_suggestion_service.current_suggestions() == []


@pytest.mark.asyncio
async def test_planning_handler_falls_back_for_legacy_create_plan_signature():
    class LegacyPlanner:
        async def create_plan(self, task_id, goal, mode, tools, memory_context=None):  # noqa: ARG002
            return Plan(task_id=task_id, goal=goal, steps=[])

    class Registry:
        def list(self):
            return []

    class Orchestrator:
        planner = LegacyPlanner()
        registry = Registry()

    handler = PlanningHandler(Orchestrator())
    plan = await handler._create_plan(Task(user_goal="g"), "g", "privacy", [], None)

    assert plan.goal == "g"
