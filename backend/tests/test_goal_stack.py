from __future__ import annotations

import pytest

from app.agents.planner_agent import PlannerAgent
from app.core import db
from app.orchestration.goal_stack import Goal, GoalStack, GoalStatus


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    db.init_db()


def test_goal_stack_push_peek_pop_and_persist():
    stack = GoalStack(scope="session-a")

    parent = stack.push("Prepare quarterly report")
    child = stack.push("Collect source spreadsheets")

    assert stack.peek() == child
    assert child.parent_goal_id == parent.id
    assert child.depth == 1

    persisted = Goal.model_validate(db.fetch_one("goals", child.id))
    assert persisted.user_goal == "Collect source spreadsheets"
    assert persisted.description == "Collect source spreadsheets"
    assert persisted.status == GoalStatus.ACTIVE

    popped = stack.pop()

    assert popped is not None
    assert popped.id == child.id
    assert popped.status == GoalStatus.COMPLETED
    assert stack.peek() == parent


def test_goal_stack_relate_task_and_planning_context():
    stack = GoalStack(scope="task-scope")
    parent = stack.push("Ship the release")
    child = stack.push("Run focused backend tests", task_id="task_1")

    related = stack.relate_task("task_2", child.id)
    stack.relate_task("task_2", child.id)
    context = stack.get_context_for_planning()

    assert related.related_task_ids == ["task_1", "task_2"]
    assert related.task_ids == ["task_1", "task_2"]
    assert context["scope"] == "task-scope"
    assert context["active_goal"]["id"] == child.id
    assert context["active_goal"]["user_goal"] == "Run focused backend tests"
    assert [item["id"] for item in context["goal_stack"]] == [parent.id, child.id]


def test_goal_model_accepts_document_contract_fields():
    goal = Goal(user_goal="Complete Phase 2", sub_goals=["GoalStack"], related_task_ids=["task_1"])

    assert goal.description == "Complete Phase 2"
    assert goal.sub_goals == ["GoalStack"]
    assert goal.task_ids == ["task_1"]


def test_goal_stack_scopes_do_not_share_active_goals():
    alpha = GoalStack(scope="alpha")
    beta = GoalStack(scope="beta")

    alpha_goal = alpha.push("Alpha goal")
    beta_goal = beta.push("Beta goal")

    assert alpha.peek() == alpha_goal
    assert beta.peek() == beta_goal


def test_goal_stack_can_find_related_goals_without_cross_linking_unrelated_tasks():
    stack = GoalStack(scope="session")
    phase_goal = stack.push("Complete Phase 2 autonomous planning", task_id="task_1", parent_goal_id="")
    stack.push("Plan a weekend grocery list", task_id="task_2", parent_goal_id="")

    related = stack.find_related("Finish Phase 2 planning tests")
    unrelated = stack.find_related("Book a dentist appointment")

    assert related is not None
    assert related.id == phase_goal.id
    assert unrelated is None


@pytest.mark.asyncio
async def test_planner_includes_goal_context_in_prompt(monkeypatch):
    captured: dict[str, str] = {}

    class CapturingProvider:
        async def structured_chat(self, messages, output_schema):  # noqa: ARG002
            captured["user"] = messages[-1]["content"]
            return {
                "goal": "Find release notes",
                "assumptions": [],
                "steps": [
                    {
                        "id": "step_1",
                        "agent_name": "FileAgent",
                        "tool_name": "file.search_by_name",
                        "description": "Search for release notes.",
                        "args": {"query": "release notes"},
                        "risk_level": "R0_READ_ONLY",
                        "depends_on": [],
                    }
                ],
            }

    monkeypatch.setattr("app.agents.planner_agent.get_provider", lambda: CapturingProvider())

    plan = await PlannerAgent().create_plan(
        "task_goal_context",
        "Find release notes",
        "efficiency",
        ["file.search_by_name"],
        goal_context={
            "scope": "phase-2",
            "active_goal": {"description": "Keep Phase 2 implementation coherent"},
            "goal_stack": [
                {"description": "Complete Phase 2"},
                {"description": "Implement GoalStack"},
            ],
        },
    )

    assert plan.goal == "Find release notes"
    assert "Goal context:" in captured["user"]
    assert "Keep Phase 2 implementation coherent" in captured["user"]
    assert "Implement GoalStack" in captured["user"]
