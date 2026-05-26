"""Tests for P0-1 subagent act/reflect autonomous reasoning."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.agents.base import AgentContext
from app.agents.browser_agent import BrowserAgent
from app.agents.computer_agent import ComputerAgent
from app.agents.document_agent import DocumentAgent
from app.agents.file_agent import FileAgent
from app.agents.app_agent import AppAgent
from app.agents.search_agent import SearchAgent
from app.core import db
from app.core.schemas import AgentAction, PlanStep, ToolResult
from app.llm.mock_provider import MockProvider
from app.policy.risk import RiskLevel
from app.tools.registry import register_all_tools
from app.tools.schemas import ToolDefinition


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    register_all_tools()
    yield


def _plan_step(tool_name: str = "file.find_duplicates", agent: str = "FileAgent") -> PlanStep:
    return PlanStep(
        task_id="task-1",
        order=0,
        agent_name=agent,
        tool_name=tool_name,
        description="Find duplicate invoices in the downloads directory",
        args={"path": "C:/Downloads"},
        expected_observation="duplicate report",
    )


def test_file_agent_allowed_tools_filters_by_owner():
    agent = FileAgent()
    allowed = agent.allowed_tools()
    assert any(name.startswith("file.") for name in allowed)
    assert "system.get_info" not in allowed
    # FileAgent's system_prompt must mention authorized directories.
    assert "authorized" in agent.system_prompt().lower()


def test_act_returns_propose_tool_for_clear_registered_step():
    provider = MockProvider()
    agent = FileAgent()
    step = _plan_step()

    action = asyncio.run(
        agent.act(step, AgentContext(task_id="task-1", mode="privacy", allowed_directories=[]), provider=provider)
    )
    assert isinstance(action, AgentAction)
    assert action.kind == "propose_tool"
    assert action.tool_name.startswith("file.")
    assert "rationale" in action.model_dump()


def test_act_uses_deterministic_fast_path_without_provider_for_clear_steps():
    class FailingProvider(MockProvider):
        async def structured_chat(self, messages, output_schema):  # noqa: ARG002
            raise AssertionError("deterministic fast path should not call provider")

    registry = register_all_tools()
    tool = registry.get("file.find_duplicates")
    tool.fast_path_eligible = True
    tool.input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    agent = FileAgent()
    step = _plan_step()

    action = asyncio.run(
        agent.act(
            step,
            AgentContext(task_id="task-1", mode="privacy", allowed_directories=[], registry=registry),
            provider=FailingProvider(),
        )
    )

    assert action.kind == "propose_tool"
    assert action.tool_name == "file.find_duplicates"
    assert action.args == {"path": "C:/Downloads"}
    assert "deterministic fast path" in action.rationale


def test_app_agent_fast_path_uses_declared_schema_without_provider():
    class FailingProvider(MockProvider):
        async def structured_chat(self, messages, output_schema):  # noqa: ARG002
            raise AssertionError("app fast path should not call provider")

    registry = register_all_tools()
    agent = AppAgent()
    step = PlanStep(
        task_id="task-1",
        order=0,
        agent_name="AppAgent",
        tool_name="app.launch_installed",
        description="Open notepad",
        args={"app": "notepad", "dry_run": True},
    )

    action = asyncio.run(
        agent.act(
            step,
            AgentContext(task_id="task-1", mode="privacy", allowed_directories=[], registry=registry),
            provider=FailingProvider(),
        )
    )

    assert action.kind == "propose_tool"
    assert action.tool_name == "app.launch_installed"
    assert action.args == {"app": "notepad", "dry_run": True}


def test_act_requests_revision_when_fast_path_detects_missing_required_args():
    registry = register_all_tools()
    agent = SearchAgent()
    step = PlanStep(
        task_id="task-1",
        order=0,
        agent_name="SearchAgent",
        tool_name="tool.search",
        description="Find a deferred tool for calendars",
        args={},
        expected_observation="tool matches",
    )

    action = asyncio.run(agent.act(step, AgentContext(task_id="task-1", mode="privacy", allowed_directories=[], registry=registry)))

    assert action.kind == "request_revision"
    assert "query" in action.rationale


def test_act_falls_back_to_provider_when_schema_is_not_explicit_enough_for_fast_path():
    class RecordingProvider(MockProvider):
        def __init__(self) -> None:
            self.calls = 0

        async def structured_chat(self, messages, output_schema):  # noqa: ARG002
            self.calls += 1
            return {
                "kind": "request_revision",
                "rationale": "path is missing",
                "follow_up_question": "Which directory should be listed?",
            }

    provider = RecordingProvider()
    registry = register_all_tools()
    registry.register(
        ToolDefinition(
            name="test.implicit_schema",
            description="implicit schema",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=lambda args, context: {"ok": True},  # noqa: ARG005
            fast_path_eligible=True,
        )
    )
    agent = FileAgent()
    step = PlanStep(
        task_id="task-1",
        order=0,
        agent_name="FileAgent",
        tool_name="test.implicit_schema",
        description="List a directory",
        args={},
    )

    action = asyncio.run(
        agent.act(
            step,
            AgentContext(task_id="task-1", mode="privacy", allowed_directories=[], registry=registry),
            provider=provider,
        )
    )

    assert provider.calls == 1
    assert action.kind == "request_revision"
    assert "directory" in action.follow_up_question.lower()


def test_act_returns_request_revision_on_provider_failure():
    class BrokenProvider(MockProvider):
        async def structured_chat(self, messages, output_schema):
            raise RuntimeError("boom")

    agent = ComputerAgent()
    step = _plan_step(tool_name="system.get_info", agent="ComputerAgent")
    failed_observation = ToolResult(
        tool_call_id="call-1",
        ok=False,
        error="previous system probe failed",
        observation="system.get_info failed",
    )
    action = asyncio.run(
        agent.act(
            step,
            AgentContext(task_id="t", mode="privacy", allowed_directories=[]),
            observation=failed_observation,
            provider=BrokenProvider(),
        )
    )
    assert action.kind == "request_revision"
    assert "failed" in action.rationale.lower()


def test_reflect_publishes_observation_to_bus():
    agent = SearchAgent()
    step = _plan_step(tool_name="search.query", agent="SearchAgent")
    step.task_id = "task-reflect"
    result = ToolResult(
        tool_call_id="call-1",
        ok=True,
        observation="3 results retrieved (source URLs preserved)",
    )
    summary = asyncio.run(agent.reflect(step, result))
    assert "SearchAgent" in summary
    # Verify bus picked it up.
    messages = agent.bus.get_messages("task-reflect")
    assert any(m.from_agent == "SearchAgent" and m.message_type.value == "observation" for m in messages)


def test_each_subagent_has_distinct_system_prompt():
    prompts = {
        agent.name: agent.system_prompt()
        for agent in [FileAgent(), DocumentAgent(), ComputerAgent(), AppAgent(), BrowserAgent(), SearchAgent()]
    }
    assert len({prompt for prompt in prompts.values()}) == 6
    # Spot-check specific guardrails per agent.
    assert "authorized directories" in prompts["FileAgent"].lower()
    assert "privacy" in prompts["DocumentAgent"].lower()
    assert "uninstall" in prompts["AppAgent"].lower()
    assert "efficiency" in prompts["BrowserAgent"].lower()
    assert "citation" in prompts["SearchAgent"].lower() or "url" in prompts["SearchAgent"].lower()


def test_mock_provider_responds_to_agent_action_schema():
    provider = MockProvider()
    schema = {
        "type": "object",
        "required": ["kind"],
        "properties": {
            "kind": {"type": "string", "enum": ["propose_tool", "request_revision", "done"]},
        },
    }
    payload = asyncio.run(
        provider.structured_chat(
            [
                {"role": "system", "content": "You are FileAgent..."},
                {"role": "user", "content": "Find duplicates in downloads"},
            ],
            schema,
        )
    )
    assert payload["kind"] == "propose_tool"
    assert payload["tool_name"].startswith("file.")
