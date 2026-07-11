from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.agents.memory_agent import MemoryAgent
from app.core import db
from app.core.schemas import MemoryState, Plan, PlanStep, StepStatus, Task
from app.orchestration.handlers.completion_handler import CompletionHandler


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


def test_remember_lesson_stores_structured_lesson():
    agent = MemoryAgent()

    memory = asyncio.run(
        agent.remember_lesson(
            {
                "goal_pattern": "organize files",
                "tool": "file.move",
                "args_pattern": {"source": "<path>"},
                "outcome": "succeeded",
                "reason": "move completed",
            },
            task_id="task_1",
        )
    )

    assert memory.kind == "lesson"
    assert "lesson" in memory.tags
    payload = json.loads(memory.content)
    assert payload["goal_pattern"] == "organize files"
    assert payload["tool"] == "file.move"


def test_completion_handler_extracts_lessons_for_successful_steps():
    class Orchestrator:
        name = "OrchestratorAgent"

        def __init__(self):
            self.memory = MemoryAgent()

    task = Task(id="task_1", user_goal="organize invoices", final_summary="done")
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[
            PlanStep(
                task_id=task.id,
                agent_name="FileAgent",
                tool_name="file.move",
                description="move invoice",
                args={"source": "C:/tmp/a.pdf", "destination": "C:/tmp/b.pdf"},
                expected_observation="invoice moved",
                status=StepStatus.SUCCEEDED,
            ),
            PlanStep(
                task_id=task.id,
                agent_name="FileAgent",
                tool_name="file.read",
                description="read invoice",
                status=StepStatus.FAILED,
            ),
        ],
    )

    handler = CompletionHandler(Orchestrator())
    asyncio.run(handler.extract_lessons(task, plan))

    lessons = [item for item in handler.orchestrator.memory.list_all() if item.kind == "lesson"]
    assert len(lessons) == 1
    payload = json.loads(lessons[0].content)
    assert payload["tool"] == "file.move"
    assert payload["args_pattern"]["source"] == "<path>"


def test_completion_auto_learning_is_disabled_by_default(monkeypatch):
    monkeypatch.setenv("LENGRVIS_MEMORY_AUTO_LEARNING_ENABLED", "false")

    class Orchestrator:
        name = "OrchestratorAgent"

        def __init__(self):
            self.memory = MemoryAgent()

    task = Task(id="task_disabled", user_goal="organize invoices", final_summary="done")
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[])
    handler = CompletionHandler(Orchestrator())

    asyncio.run(handler.consolidate_memory(task, plan))

    assert handler.orchestrator.memory.list_all() == []


def test_completion_auto_learning_opt_in_writes_to_quarantine(monkeypatch):
    monkeypatch.setenv("LENGRVIS_MEMORY_AUTO_LEARNING_ENABLED", "true")

    class Orchestrator:
        name = "OrchestratorAgent"

        def __init__(self):
            self.memory = MemoryAgent()

    task = Task(id="task_enabled", user_goal="organize invoices", final_summary="done")
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[])
    handler = CompletionHandler(Orchestrator())

    asyncio.run(handler.consolidate_memory(task, plan))

    memories = handler.orchestrator.memory.list_all()
    assert len(memories) == 1
    assert memories[0].kind == "task_summary"
    assert memories[0].state == MemoryState.QUARANTINED
    assert asyncio.run(handler.orchestrator.memory.recall("organize invoices")) == []
