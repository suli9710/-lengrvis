from __future__ import annotations

import pytest

from app.agents.planner_agent import PlannerAgent
from app.core import db
from app.core.session_context import SessionContext, SessionContextStore, reset_session_context_store
from app.core.schemas import Plan, Task
from app.orchestration.handlers.planning_handler import PlanningHandler


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    reset_session_context_store()
    db.init_db()
    yield
    reset_session_context_store()


def test_session_context_persists_and_loads_by_session_id():
    store = SessionContextStore(session_id="session_a")
    store.load_latest()
    store.remember_task("task_1", workflow_state={"phase": "editing"})
    store.learn_preference("editor", "WPS")

    reloaded = SessionContextStore(session_id="session_a").load()
    isolated = SessionContextStore(session_id="session_b").load()

    assert reloaded.id == "session_a"
    assert reloaded.unfinished_task_ids == ["task_1"]
    assert reloaded.current_workflow_state["phase"] == "editing"
    assert reloaded.learned_preferences["editor"] == "WPS"
    assert isolated.id == "session_b"
    assert isolated.unfinished_task_ids == []


def test_session_context_persists_conversation_summary():
    store = SessionContextStore(session_id="session_a")
    store.load_latest()
    store.remember_summary(
        "Earlier work summary.",
        last_message_id="msg_1",
        token_stats={"projected_tokens": 42},
    )

    reloaded = SessionContextStore(session_id="session_a").load()

    assert reloaded.conversation_summary == "Earlier work summary."
    assert reloaded.last_summarized_message_id == "msg_1"
    assert reloaded.token_stats["projected_tokens"] == 42


def test_session_context_complete_task_removes_unfinished_reference():
    store = SessionContextStore(session_id="session_a")
    store.load_latest()
    store.remember_task("task_1")
    store.complete_task("task_1")

    assert store.current.unfinished_task_ids == []


def test_session_context_load_latest_uses_configured_session_id():
    older_created_later = SessionContext(id="created_later", created_at="2026-01-02T00:00:00Z", updated_at="2026-01-02T00:00:00Z")
    newer_updated = SessionContext(
        id="updated_later",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-03T00:00:00Z",
        unfinished_task_ids=["task_newer"],
    )
    db.upsert_model("session_contexts", older_created_later)
    db.upsert_model("session_contexts", newer_updated)
    with db.connect() as conn:
        conn.execute("UPDATE session_contexts SET updated_at = ? WHERE id = ?", ("2026-01-02T00:00:00Z", "created_later"))
        conn.execute("UPDATE session_contexts SET updated_at = ? WHERE id = ?", ("2026-01-03T00:00:00Z", "updated_later"))

    loaded = SessionContextStore(session_id="session_c").load_latest()

    assert loaded.id == "session_c"
    assert loaded.unfinished_task_ids == []


def test_planner_formats_session_context_for_prompt():
    block = PlannerAgent()._format_session_context(
        {
            "current_workflow_state": {"app": "WPS", "document": "report.docx"},
            "unfinished_task_ids": ["task_1"],
            "learned_preferences": {"confirm_before_wps_save": True},
            "notes": ["User wants concise file names."],
            "conversation_summary": "The report task is halfway done.",
        }
    )

    assert "Session continuity context" in block
    assert "task_1" in block
    assert "confirm_before_wps_save" in block
    assert "halfway done" in block


@pytest.mark.asyncio
async def test_planning_handler_passes_session_context_to_planner():
    captured = {}

    class Planner:
        async def create_plan(self, task_id, goal, mode, tools, memory_context=None, perception_context=None, goal_context=None, session_context=None):  # noqa: ARG002
            captured["session_context"] = session_context
            return Plan(task_id=task_id, goal=goal, steps=[])

    class Registry:
        def list(self):
            return []

    class Orchestrator:
        planner = Planner()
        registry = Registry()
        session_context_store = SessionContextStore(session_id="session_test")

    task = Task(id="task_session", user_goal="continue report")
    handler = PlanningHandler(Orchestrator())
    plan = await handler._create_plan(task, task.user_goal, "efficiency", [], None, {"unfinished_task_ids": ["old_task"]})

    assert plan.goal == "continue report"
    assert captured["session_context"]["unfinished_task_ids"] == ["old_task"]
