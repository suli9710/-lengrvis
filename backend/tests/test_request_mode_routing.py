"""Request/matter privacy must reach real provider and embedding boundaries."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.agents.base import AgentContext
from app.agents.file_agent import FileAgent
from app.agents.memory_agent import MemoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.config import AppSettings
from app.core import db
from app.core.butler_models import ButlerMatter, ButlerMessage
from app.core.schemas import PlanStep, Task, ToolResult
from app.indexer import embedding_service
from app.llm import registry
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.main import app
from app.policy.risk import RiskLevel
from app.services.butler_service import ButlerCoordinator
from app.services.task_service import handle_chat
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolDefinition


@pytest.fixture
def routing(monkeypatch):
    monkeypatch.setattr("app.core.outbound_url._system_resolve_outbound_ips", lambda host: ("93.184.216.34",))
    settings = AppSettings(
        mode="efficiency",
        provider_name="openai_compatible",
        base_url="https://example.com/v1",
        api_key="routing-test-placeholder",  # noqa: S106 - inert test credential.
        allow_file_content_upload=True,
        allow_mock_fallback=False,
    )
    state = {"settings": settings, "local_available": True}
    calls = []
    local = SimpleNamespace(kind="ollama", base_url="http://127.0.0.1:11434/v1", models=["test-local"])
    monkeypatch.setattr(registry, "get_effective_settings", lambda: state["settings"])
    monkeypatch.setattr(embedding_service, "get_effective_settings", lambda: state["settings"])
    monkeypatch.setattr(registry, "detect_local_backend", lambda: local if state["local_available"] else None)
    monkeypatch.setattr(registry, "detect_onnx_backend", lambda settings: None)
    monkeypatch.setattr(embedding_service, "get_local_embedding_provider", lambda settings: None)

    async def structured_chat(provider, messages, output_schema):
        calls.append(("chat", provider.settings.mode, provider.settings.base_url, str(messages)))
        if "delegate" in output_schema["properties"]:
            return {"delegate": False, "reply": "local response", "agent_hint": ""}
        return {"kind": "done", "rationale": "local decision"}

    async def embed(provider, texts, model=None):
        calls.append(("embed", provider.settings.mode, provider.settings.base_url, str(texts)))
        return [[1.0, 0.0] for _ in texts]

    # Intercept before network sending, while retaining registry routing and
    # ContextAwareProvider. These spies also detect swallowed cloud failures.
    monkeypatch.setattr(OpenAICompatibleProvider, "structured_chat", structured_chat)
    monkeypatch.setattr(OpenAICompatibleProvider, "embed", embed)
    db.init_db()
    return state, calls


def assert_private_calls(calls, local_available, marker):
    assert len(calls) == (1 if local_available else 0)
    for _operation, mode, base_url, content in calls:
        assert mode == "privacy"
        assert base_url == "http://127.0.0.1:11434/v1"
        assert marker in content


def worker_step(task_id=""):
    tools = ToolRegistry()
    tools.register(
        ToolDefinition(
            name="test.reasoning",
            description="Requires worker reasoning",
            input_schema={},
            output_schema={},
            risk_level=RiskLevel.R0_READ_ONLY,
            agent_owner="FileAgent",
            supports_dry_run=False,
            requires_authorized_path=False,
            execute=lambda args, context: {},
        )
    )
    step = PlanStep(
        task_id=task_id,
        agent_name="FileAgent",
        tool_name="test.reasoning",
        description="private-worker-marker",
    )
    return tools, step


@pytest.mark.asyncio
@pytest.mark.parametrize("local_available", [True, False])
async def test_chat_privacy_overrides_global_cloud_settings(routing, local_available):
    state, calls = routing
    state["local_available"] = local_available
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/chat", json={"message": "private-chat-marker: let's talk", "mode": "privacy"}
        )
    assert response.status_code == 200
    assert response.json()["delegated"] is False
    assert response.json()["message"]
    assert_private_calls(calls, local_available, "private-chat-marker")
    assert state["settings"].mode == "efficiency"


@pytest.mark.asyncio
@pytest.mark.parametrize("local_available", [True, False])
@pytest.mark.parametrize("recovery", [True, False])
async def test_worker_privacy_covers_reasoning_and_error_recovery(routing, local_available, recovery):
    state, calls = routing
    state["local_available"] = local_available
    tools, step = worker_step()
    observation = ToolResult(tool_call_id="test", ok=False, error="private-failure-marker") if recovery else None
    action = await FileAgent().act(
        step, AgentContext(task_id="", mode="privacy", allowed_directories=[], registry=tools), observation
    )
    assert action.kind == ("done" if local_available else "request_revision")
    assert_private_calls(calls, local_available, "private-failure-marker" if recovery else "private-worker-marker")


@pytest.mark.asyncio
@pytest.mark.parametrize("local_available", [True, False])
async def test_orchestrator_preserves_in_flight_task_mode_after_global_change(routing, local_available):
    state, calls = routing
    state["local_available"] = local_available
    task = Task(user_goal="private-worker-marker", mode="privacy")
    db.upsert_model("tasks", task)
    state["settings"] = state["settings"].model_copy(update={"mode": "hybrid"})
    orchestrator = OrchestratorAgent()
    orchestrator.registry, step = worker_step(task.id)
    await orchestrator._consult_subagent(task, step)
    assert_private_calls(calls, local_available, "private-worker-marker")
    assert state["settings"].mode == "hybrid"


@pytest.mark.asyncio
@pytest.mark.parametrize("local_available", [True, False])
async def test_butler_preference_save_binds_mode_before_task_exists(routing, local_available):
    state, calls = routing
    state["local_available"] = local_available
    response = await ButlerCoordinator().message(
        ButlerMessage(message="remember my preference: private-pref-marker", mode="privacy")
    )
    assert response["matter"]["mode"] == "privacy"
    assert response["matter"]["completion_evidence"][0]["kind"] == "memory"
    assert db.fetch_many("tasks") == []
    assert_private_calls(calls, local_available, "private-pref-marker")


@pytest.mark.asyncio
@pytest.mark.parametrize("local_available", [True, False])
async def test_butler_preference_recall_binds_persisted_matter_mode(routing, local_available):
    state, calls = routing
    state["local_available"] = local_available
    await MemoryAgent().remember("private-preference-marker", kind="preference", embedding=[1.0, 0.0])
    context = await ButlerCoordinator().preference_context(ButlerMatter(goal="private-goal-marker", mode="privacy"))
    assert context["confirmed"][0]["content"] == "private-preference-marker"
    assert_private_calls(calls, local_available, "private-goal-marker")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["remember", "recall", "remember_lesson"])
async def test_memory_task_mode_is_authoritative_after_global_change(routing, operation):
    _state, calls = routing
    task = Task(user_goal="private-memory-marker", mode="privacy")
    db.upsert_model("tasks", task)
    agent = MemoryAgent()
    if operation == "remember_lesson":
        await agent.remember_lesson({"goal_pattern": "private-memory-marker"}, task_id=task.id)
    else:
        await agent.remember("private-memory-marker", embedding=[1.0, 0.0])
        await getattr(agent, operation)("private-memory-marker", task_id=task.id)
    assert_private_calls(calls, True, "private-memory-marker")


@pytest.mark.asyncio
async def test_parallel_request_modes_do_not_mutate_or_share_global_settings(routing):
    import asyncio

    state, calls = routing
    await asyncio.gather(
        handle_chat("private-chat-marker: let's talk", "privacy"),
        handle_chat("public-chat-marker: let's talk", "efficiency"),
    )
    assert {(mode, base_url) for _, mode, base_url, _ in calls} == {
        ("privacy", "http://127.0.0.1:11434/v1"),
        ("efficiency", "https://example.com/v1"),
    }
    cloud_content = " ".join(content for _, mode, _, content in calls if mode == "efficiency")
    assert "public-chat-marker" in cloud_content
    assert "private-chat-marker" not in cloud_content
    assert state["settings"].mode == "efficiency"


@pytest.mark.asyncio
async def test_cloud_embedding_spy_detects_the_efficiency_positive_control(routing):
    _state, calls = routing
    memory = await MemoryAgent().remember("public-memory-marker")
    assert memory.embedding_dim == 2
    assert len(calls) == 1
    operation, mode, base_url, content = calls[0]
    assert (operation, mode, base_url) == ("embed", "efficiency", "https://example.com/v1")
    assert "public-memory-marker" in content
