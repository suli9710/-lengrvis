"""End-to-end parity tests covering 3 Marvis demo scenarios.

Scenario 1 — Subagent + Memory + Schedule wiring: a pre-seeded memory feeds
the planner, the orchestrator runs a step, the subagent reflects, and on
COMPLETED the orchestrator consolidates a task_summary memory.

Scenario 2 — MCP tools discovered via ToolRegistry: a mock MCP server is
adapted to ToolDefinitions and registered alongside built-ins so the
registry exposes ``mcp.<server>.<tool>`` entries.

Scenario 3 — Privacy mode blocks browser writes: the safety review gate
records a DENY in ``safety_reviews`` when a browser write step runs while
``mode=privacy``.

The tests construct deterministic plans via a PlannerAgent spy so we do not
depend on any external LLM provider.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import threading
from pathlib import Path
from typing import Any

import pytest

from app.agents.memory_agent import MemoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.planner_agent import PlannerAgent
from app.config import AppSettings
from app.core import db
from app.core.schemas import Plan, PlanStep
from app.orchestration.task_phase import TaskPhase
from app.mcp import MCPRegistry
from app.policy.risk import RiskLevel
from app.tools.registry import register_all_tools, registry as tool_registry


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


@pytest.fixture
def memory_seed_capture(monkeypatch):
    """Replace PlannerAgent.create_plan with a spy that records inputs and
    returns a single read-only step the orchestrator can execute end-to-end."""

    captured: dict[str, Any] = {"memory_context": None, "calls": 0}

    async def spy_create_plan(self, task_id, goal, mode, tools, memory_context=None):  # noqa: ARG001
        captured["memory_context"] = list(memory_context or [])
        captured["calls"] += 1
        step = PlanStep(
            task_id=task_id,
            order=1,
            agent_name="ComputerAgent",
            tool_name="system.get_info",
            description="Inspect system info to honor user memory.",
            args={},
            expected_observation="system.get_info completed.",
            risk_level=RiskLevel.R0_READ_ONLY,
            requires_approval=False,
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["Deterministic plan for E2E parity test."],
            steps=[step],
            global_risk_level=RiskLevel.R0_READ_ONLY,
            requires_user_approval=False,
        )

    monkeypatch.setattr(PlannerAgent, "create_plan", spy_create_plan)
    return captured


@pytest.fixture
def browser_write_plan(monkeypatch):
    """Spy planner that emits a single browser.click_element step. Lets us
    drive the orchestrator straight into the browser-write safety gate."""

    async def spy_create_plan(self, task_id, goal, mode, tools, memory_context=None):  # noqa: ARG001
        step = PlanStep(
            task_id=task_id,
            order=1,
            agent_name="BrowserAgent",
            tool_name="browser.click_element",
            description="Click a button on example.com.",
            args={"url": "https://example.com", "selector": "#go"},
            expected_observation="browser.click_element completed.",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_approval=True,
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["Deterministic plan for browser-write safety gate test."],
            steps=[step],
            global_risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_user_approval=True,
        )

    monkeypatch.setattr(PlannerAgent, "create_plan", spy_create_plan)


_MOCK_TOOLS = [
    {
        "name": "echo",
        "description": "Echo the provided text back",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
]


def _make_mcp_handler():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):  # noqa: D401
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            method = payload.get("method")
            if method == "tools/list":
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"tools": _MOCK_TOOLS}}
            elif method == "tools/call":
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"echo": "ok"}}
            else:
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32600, "message": "invalid method"}}
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@pytest.fixture
def mock_mcp_server():
    server = socketserver.TCPServer(("127.0.0.1", 0), _make_mcp_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        yield url
    finally:
        server.shutdown()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Scenario 1: subagent + memory + reflect + memory consolidation
# ---------------------------------------------------------------------------


def test_scenario_1_memory_planner_reflect_consolidation(memory_seed_capture):
    seed_agent = MemoryAgent()
    asyncio.run(
        seed_agent.remember(
            "user prefers monthly invoice archiving",
            tags=["preference", "invoice"],
        )
    )
    seed_ids = {item.id for item in seed_agent.list_all()}
    assert seed_ids, "Seed memory should be persisted before the orchestrator runs."

    orchestrator = OrchestratorAgent()
    task = asyncio.run(orchestrator.handle_user_goal("整理重复发票", "privacy"))

    assert task.status == TaskPhase.COMPLETED, f"Task should complete, got {task.status}: {task.final_summary}"

    # The planner spy was called with non-empty memory context (seed memory recalled).
    assert memory_seed_capture["calls"] == 1
    recalled = memory_seed_capture["memory_context"]
    assert recalled, "PlannerAgent should receive memory context recalled from seeded memories."
    assert any("invoice" in (getattr(item, "content", "") or "") for item in recalled)

    # Subagent.reflect published at least one observation tagged reflection=True.
    bus_messages = orchestrator.bus.get_messages(task.id)
    reflections = [
        m
        for m in bus_messages
        if isinstance(m.structured_payload, dict) and m.structured_payload.get("reflection") is True
    ]
    assert reflections, "Subagent should have published at least one reflect() observation."

    # After FINAL_REVIEW the orchestrator consolidated a task_summary memory.
    all_memories = MemoryAgent().list_all()
    summaries = [m for m in all_memories if m.kind == "task_summary" and m.task_id == task.id]
    assert summaries, "OrchestratorAgent should consolidate a task_summary memory on COMPLETED."


# ---------------------------------------------------------------------------
# Scenario 2: MCP tools discovered via ToolRegistry
# ---------------------------------------------------------------------------


def test_scenario_2_mcp_tools_visible_via_tool_registry(mock_mcp_server):
    settings = AppSettings(
        provider_name="mock",
        mcp_servers=[
            {"name": "demo", "url": mock_mcp_server, "transport": "http", "enabled": True}
        ],
    )

    mcp_registry = MCPRegistry()
    mcp_registry.load_from_settings(settings)
    mcp_definitions = asyncio.run(mcp_registry.adapt_to_tool_definitions())
    assert mcp_definitions, "Mock MCP server should produce at least one ToolDefinition."

    register_all_tools(extra_definitions=mcp_definitions)

    registered_names = {tool.name for tool in tool_registry.list()}
    assert "mcp.demo.echo" in registered_names, (
        f"Expected mcp.demo.echo in registry, got: {sorted(n for n in registered_names if n.startswith('mcp.'))}"
    )

    echo_tool = tool_registry.get("mcp.demo.echo")
    assert echo_tool.agent_owner == "SearchAgent"
    assert echo_tool.risk_level == RiskLevel.R0_READ_ONLY


# ---------------------------------------------------------------------------
# Scenario 3: privacy mode blocks browser writes via the safety gate
# ---------------------------------------------------------------------------


def test_scenario_3_browser_write_denied_in_privacy_mode(browser_write_plan):
    orchestrator = OrchestratorAgent()
    task = asyncio.run(orchestrator.handle_user_goal("点击 example.com 的按钮", "privacy"))

    review_rows = db.fetch_many("safety_reviews", "task_id = ?", (task.id,), limit=200)
    assert review_rows, "Expected safety_reviews rows for this task."

    deny_rows = [
        row
        for row in review_rows
        if row.get("verdict") == "deny"
        and any("privacy" in (reason or "").lower() for reason in row.get("reasons") or [])
    ]
    assert deny_rows, (
        "Privacy mode should DENY a browser.click_element step with a privacy-mode reason."
        f" Got: {[(r.get('target_type'), r.get('verdict'), r.get('reasons')) for r in review_rows]}"
    )
