from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.core import db
from app.core.schemas import MessageType
from app.orchestration.agent_bus import AgentBus
from app.orchestration.orchestrator_registry import OrchestratorRegistry


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    yield


def test_registry_reuses_orchestrator_and_bus_per_task() -> None:
    registry = OrchestratorRegistry()
    first = SimpleNamespace(bus=AgentBus())
    second = SimpleNamespace(bus=AgentBus())

    registry.bind(task_id="task_a", orchestrator=first, run_id="run_a")
    assert registry.get_for_task("task_a") is first
    assert registry.get_for_run("run_a") is first
    assert registry.bus_for_task("task_a") is first.bus

    registry.bind(task_id="task_a", orchestrator=second, run_id="run_b")
    assert registry.get_for_task("task_a") is second
    assert registry.get_for_run("run_b") is second

    registry.release_run("run_b")
    assert registry.get_for_run("run_b") is None
    assert registry.get_for_task("task_a") is second

    registry.release_task("task_a")
    assert registry.get_for_task("task_a") is None


def test_terminal_run_releases_registry_binding_but_paused_run_keeps_it() -> None:
    """R4-M5 guard: finished runs must free the per-task orchestrator cache."""
    from app.core.schemas import Run, RunEngine, RunPhase
    from app.orchestration.orchestrator_registry import orchestrator_registry
    from app.services.run_service import _release_terminal_orchestrator

    orchestrator = SimpleNamespace(bus=AgentBus())

    # Paused run: binding must survive (resume reuses the same bus).
    paused = Run(message="goal", mode="efficiency", engine=RunEngine.OS, phase=RunPhase.PAUSED, task_id="task_m5")
    db.upsert_model("runs", paused)
    orchestrator_registry.bind(task_id="task_m5", orchestrator=orchestrator, run_id=paused.id)
    _release_terminal_orchestrator(paused.id)
    assert orchestrator_registry.get_for_task("task_m5") is orchestrator

    # Terminal run with no other live runs on the task: binding released.
    completed = Run(
        message="goal", mode="efficiency", engine=RunEngine.OS, phase=RunPhase.COMPLETED, task_id="task_m5_done"
    )
    db.upsert_model("runs", completed)
    orchestrator_registry.bind(task_id="task_m5_done", orchestrator=orchestrator, run_id=completed.id)
    _release_terminal_orchestrator(completed.id)
    assert orchestrator_registry.get_for_task("task_m5_done") is None
    assert orchestrator_registry.get_for_run(completed.id) is None

    # Terminal run whose task still has a live sibling run: task binding kept.
    shared_done = Run(
        message="goal", mode="efficiency", engine=RunEngine.OS, phase=RunPhase.COMPLETED, task_id="task_m5_shared"
    )
    shared_live = Run(
        message="goal", mode="efficiency", engine=RunEngine.OS, phase=RunPhase.PAUSED, task_id="task_m5_shared"
    )
    db.upsert_model("runs", shared_done)
    db.upsert_model("runs", shared_live)
    orchestrator_registry.bind(task_id="task_m5_shared", orchestrator=orchestrator, run_id=shared_done.id)
    _release_terminal_orchestrator(shared_done.id)
    assert orchestrator_registry.get_for_task("task_m5_shared") is orchestrator
    orchestrator_registry.release_task("task_m5_shared")
    orchestrator_registry.release_task("task_m5")


def test_agent_bus_instances_do_not_share_subscriptions() -> None:
    async def run() -> None:
        publisher = AgentBus()
        subscriber = AgentBus()
        queue = subscriber.subscribe("task_iso")
        try:
            publisher.publish_text("task_iso", "PlannerAgent", "hidden", message_type=MessageType.PROPOSAL)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(queue.get(), timeout=0.05)
            publisher = subscriber
            publisher.publish_text("task_iso", "PlannerAgent", "visible", message_type=MessageType.PROPOSAL)
            message = await asyncio.wait_for(queue.get(), timeout=1)
            assert message.content == "visible"
        finally:
            subscriber.unsubscribe("task_iso", queue)

    asyncio.run(run())


def test_orchestrator_registry_includes_mcp_tools_from_global() -> None:
    from app.agents.orchestrator_agent import OrchestratorAgent
    from app.policy.risk import RiskLevel
    from app.tools.registry import registry as global_registry
    from app.tools.schemas import ToolDefinition

    def execute(args, context):  # noqa: ANN001, ANN202, ARG001
        return {"ok": True, "echo": args}

    global_registry.register(
        ToolDefinition(
            name="mcp.test.sync_probe",
            description="MCP sync probe",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
            agent_owner="SearchAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=execute,
            trust_tier="third_party",
            effects=["external_call"],
        )
    )
    try:
        orchestrator = OrchestratorAgent()
        tool = orchestrator.registry.get("mcp.test.sync_probe")
        assert tool.name == "mcp.test.sync_probe"
    finally:
        global_registry._tools.pop("mcp.test.sync_probe", None)
