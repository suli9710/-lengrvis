"""R6 multi-agent fix: supervisor agent_hint must reach Planner."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.planner_agent import (
    PlannerAgent,
    format_supervisor_hint_block,
    normalize_supervisor_agent_hint,
    supervisor_hint_allows_deterministic,
)
from app.agents.supervisor_agent import SupervisorDecision
from app.core import db
from app.core.schemas import Task
from app.llm.mock_provider import MockProvider
from app.orchestration.handlers.planning_handler import PlanningHandler
from app.services import task_service


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()
    yield


def test_normalize_supervisor_agent_hint_filters_unknown():
    assert normalize_supervisor_agent_hint("FileAgent") == "FileAgent"
    assert normalize_supervisor_agent_hint("OrchestratorAgent") == ""
    assert normalize_supervisor_agent_hint("EvilAgent") == ""


def test_supervisor_hint_block_only_for_worker_agents():
    assert "BrowserAgent" in format_supervisor_hint_block("BrowserAgent")
    assert format_supervisor_hint_block("OrchestratorAgent") == ""


def test_deterministic_search_skipped_when_hint_is_browser():
    planner = PlannerAgent()
    goal = "帮我找一下文件：季度报告"
    plan = planner._deterministic_search_plan("task-1", goal, ["file.search_by_name"], agent_hint="BrowserAgent")
    assert plan is None
    plan = planner._deterministic_search_plan("task-1", goal, ["file.search_by_name"], agent_hint="FileAgent")
    assert plan is not None
    assert plan.steps[0].tool_name == "file.search_by_name"


def test_supervisor_hint_allows_deterministic_matrix():
    assert supervisor_hint_allows_deterministic(None, "FileAgent")
    assert supervisor_hint_allows_deterministic("FileAgent", "FileAgent")
    assert not supervisor_hint_allows_deterministic("BrowserAgent", "FileAgent")


@pytest.mark.anyio
async def test_create_plan_includes_supervisor_hint_in_prompt(monkeypatch):
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    captured: dict[str, str] = {}

    async def spy_structured_chat(self, messages, output_schema):  # noqa: ARG001
        captured["user"] = next(m["content"] for m in reversed(messages) if m.get("role") == "user")
        return {
            "goal": "read page",
            "steps": [
                {
                    "id": "step_1",
                    "agent_name": "BrowserAgent",
                    "tool_name": "browser.read_page",
                    "description": "Read page",
                    "args": {"url": "https://example.com"},
                    "depends_on": [],
                }
            ],
        }

    monkeypatch.setattr("app.llm.mock_provider.MockProvider.structured_chat", spy_structured_chat)

    plan = await PlannerAgent().create_plan(
        "task-hint",
        "读取 example.com",
        "efficiency",
        ["browser.read_page", "file.search_by_name"],
        agent_hint="BrowserAgent",
    )
    assert "Supervisor routing hint: BrowserAgent" in captured["user"]
    assert plan.steps[0].tool_name == "browser.read_page"


@pytest.mark.anyio
async def test_planning_handler_retries_with_revision_feedback(monkeypatch):
    calls: list[dict[str, str | None]] = []

    async def spy_create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ARG001
        calls.append({"revision_feedback": kwargs.get("planner_revision_feedback")})
        from app.core.schemas import Plan, PlanStep

        if len(calls) == 1:
            return Plan(
                task_id=task_id,
                goal=goal,
                steps=[
                    PlanStep(
                        id="step_1",
                        task_id=task_id,
                        order=1,
                        agent_name="FileAgent",
                        tool_name="file.search_by_name",
                        description="search",
                        args={"query": "report"},
                    )
                ],
            )
        return Plan(
            task_id=task_id,
            goal=goal,
            steps=[
                PlanStep(
                    id="step_1",
                    task_id=task_id,
                    order=1,
                    agent_name="BrowserAgent",
                    tool_name="browser.read_page",
                    description="read",
                    args={"url": "https://example.com"},
                )
            ],
        )

    monkeypatch.setattr("app.agents.planner_agent.PlannerAgent.create_plan", spy_create_plan)

    class BrowserTool:
        name = "browser.read_page"
        description = "Read page"
        agent_owner = "BrowserAgent"
        defer_loading = False
        risk_level = __import__("app.policy.risk", fromlist=["RiskLevel"]).RiskLevel.R0_READ_ONLY
        input_schema = {}
        effects = []
        resource_kinds = []
        trust_tier = "builtin"
        tool_version = "1"

        def is_model_visible(self):
            return True

    class FileSearchTool:
        name = "file.search_by_name"
        description = "Search"
        agent_owner = "FileAgent"
        defer_loading = False
        risk_level = __import__("app.policy.risk", fromlist=["RiskLevel"]).RiskLevel.R0_READ_ONLY
        input_schema = {}
        effects = []
        resource_kinds = []
        trust_tier = "builtin"
        tool_version = "1"

        def is_model_visible(self):
            return True

    class StubOrchestrator:
        name = "OrchestratorAgent"
        planner = PlannerAgent()

        def _supervise_new_agent_messages(self, task_id, stage):  # noqa: ARG002
            return True

        class registry:
            @staticmethod
            def list_for_planning():
                return [BrowserTool(), FileSearchTool()]

            @staticmethod
            def list():
                return [BrowserTool(), FileSearchTool()]

            @staticmethod
            def get(name):  # noqa: ARG004
                if name == "browser.read_page":
                    return BrowserTool()
                if name == "file.search_by_name":
                    return FileSearchTool()
                raise KeyError(name)

    handler = PlanningHandler(StubOrchestrator())  # type: ignore[arg-type]
    task = Task(user_goal="读取网页", mode="efficiency")
    plan = await handler._create_plan(task, task.user_goal, task.mode, [], agent_hint="BrowserAgent")

    assert len(calls) == 2
    assert calls[0]["revision_feedback"] is None
    assert calls[1]["revision_feedback"] is not None
    assert "file.search_by_name" in calls[1]["revision_feedback"]
    assert plan.steps[0].tool_name == "browser.read_page"


@pytest.mark.anyio
async def test_planning_handler_reads_task_metadata_hint(monkeypatch):
    captured: dict[str, str | None] = {}

    async def spy_create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ARG001
        captured["agent_hint"] = kwargs.get("agent_hint")
        from app.core.schemas import Plan, PlanStep

        return Plan(
            task_id=task_id,
            goal=goal,
            steps=[
                PlanStep(
                    id="step_1",
                    task_id=task_id,
                    order=1,
                    agent_name="BrowserAgent",
                    tool_name="browser.read_page",
                    description="read",
                    args={"url": "https://example.com"},
                )
            ],
        )

    monkeypatch.setattr("app.agents.planner_agent.PlannerAgent.create_plan", spy_create_plan)

    class BrowserTool:
        name = "browser.read_page"
        description = "Read page"
        agent_owner = "BrowserAgent"
        defer_loading = False
        risk_level = __import__("app.policy.risk", fromlist=["RiskLevel"]).RiskLevel.R0_READ_ONLY
        input_schema = {}
        effects = []
        resource_kinds = []
        trust_tier = "builtin"
        tool_version = "1"

        def is_model_visible(self):
            return True

    class StubOrchestrator:
        name = "OrchestratorAgent"
        planner = PlannerAgent()

        def _supervise_new_agent_messages(self, task_id, stage):  # noqa: ARG002
            return True

        class safety:
            @staticmethod
            def review_goal(task_id, goal):  # noqa: ARG002
                from app.core.schemas import SafetyReview
                from app.policy.risk import RiskLevel, SafetyVerdict

                return SafetyReview(
                    task_id=task_id,
                    target_type="goal",
                    verdict=SafetyVerdict.ALLOW,
                    risk_level=RiskLevel.R0_READ_ONLY,
                    reasons=[],
                )

        class consultation_handler:
            @staticmethod
            def consult_and_review(task, plan):  # noqa: ARG002
                from app.core.schemas import SafetyReview
                from app.policy.risk import RiskLevel, SafetyVerdict

                return SafetyReview(
                    task_id=task.id,
                    target_type="plan",
                    verdict=SafetyVerdict.ALLOW,
                    risk_level=RiskLevel.R0_READ_ONLY,
                    reasons=[],
                )

        async def _recall_memory(self, goal):  # noqa: ARG002
            return []

        async def _process_steps(self, task, plan):  # noqa: ARG002
            return None

        class completion_handler:
            @staticmethod
            async def finalize(task, plan):  # noqa: ARG002
                return None

        def _set_status(self, task, status, final_summary=None):  # noqa: ARG002
            task.status = status
            if final_summary is not None:
                task.final_summary = final_summary
            return task

        class registry:
            @staticmethod
            def list_for_planning():
                return [BrowserTool()]

            @staticmethod
            def list():
                return [BrowserTool()]

            @staticmethod
            def get(name):  # noqa: ARG004
                if name == "browser.read_page":
                    return BrowserTool()
                raise KeyError(name)

    handler = PlanningHandler(StubOrchestrator())  # type: ignore[arg-type]
    task = Task(user_goal="读取网页", mode="efficiency", metadata={"supervisor_agent_hint": "BrowserAgent"})
    await handler._create_plan(task, task.user_goal, task.mode, [], agent_hint="BrowserAgent")
    assert captured["agent_hint"] == "BrowserAgent"


@pytest.mark.anyio
async def test_mock_provider_extracts_supervisor_hint_before_user_goal():
    provider = MockProvider()
    prompt = (
        "Supervisor routing hint: SearchAgent\n"
        "Prefer tools owned by SearchAgent when they satisfy the user goal.\n\n"
        "Mode: efficiency\nAvailable tools:\n- search.query\nUser goal: 搜索 AI 新闻"
    )
    plan = await provider.structured_chat(
        [{"role": "user", "content": prompt}],
        {"type": "object", "properties": {"steps": {"type": "array"}}, "required": ["steps"]},
    )
    assert plan["steps"][0]["tool_name"] == "search.query"


def test_delegate_task_persists_supervisor_agent_hint(monkeypatch):
    captured: dict[str, object] = {}

    def fake_create_task_shell(self, goal, mode, *, metadata=None):  # noqa: ARG001
        captured["metadata"] = dict(metadata or {})
        from app.core.schemas import TaskStatus

        return Task(user_goal=goal, mode=mode, status=TaskStatus.PLANNING, metadata=dict(metadata or {}))

    monkeypatch.setattr(
        "app.services.task_service.OrchestratorAgent.create_task_shell",
        fake_create_task_shell,
    )
    monkeypatch.setattr("app.services.task_service.orchestrator_registry.bind", lambda **kwargs: None)

    class StubTaskPool:
        def submit_nowait(self, task, runner):  # noqa: ANN001
            captured["submitted_task"] = task
            captured["runner"] = runner

    monkeypatch.setattr("app.services.task_service.get_pool", lambda: StubTaskPool())

    response = asyncio.run(
        task_service._delegate_task(
            "读取 https://example.com",
            "efficiency",
            SupervisorDecision(delegate=True, reply="ok", agent_hint="BrowserAgent"),
        )
    )
    assert response.agent == "BrowserAgent"
    assert captured["metadata"] == {"supervisor_agent_hint": "BrowserAgent"}
    assert captured["submitted_task"].id == response.task_id
