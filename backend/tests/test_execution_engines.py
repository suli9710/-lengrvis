from __future__ import annotations

import pytest

from app.config import AppSettings
from app.orchestration.developer_engine import DeveloperExecutionEngine, readonly_developer_tool_names
from app.orchestration.engine_router import EngineRouter, configured_default_engine, configured_max_turns, route_engine
from app.orchestration.execution_engine import InMemoryRunStore
from app.orchestration.execution_models import RunPhase


def test_route_engine_auto_selects_developer_for_repo_goals() -> None:
    decision = route_engine("fix failing backend pytest around planner imports")

    assert decision.selected_engine == "developer"
    assert decision.requested_engine == "auto"


def test_route_engine_auto_selects_os_for_browser_goals() -> None:
    decision = route_engine("open the browser and click the account settings")

    assert decision.selected_engine == "os"


def test_route_engine_explicit_override_wins() -> None:
    decision = route_engine("fix backend tests", requested_engine="os")

    assert decision.selected_engine == "os"
    assert decision.reason == "explicit engine override"


def test_default_engine_env_hooks_accept_agent_loop_names() -> None:
    env = {
        "MARVIS_DEFAULT_ENGINE": "developer",
        "MARVIS_AGENT_LOOP_MAX_TURNS": "5",
    }

    assert configured_default_engine(env) == "developer"
    assert configured_max_turns(env) == 5


def test_default_engine_env_keeps_legacy_agent_loop_name() -> None:
    assert configured_default_engine({"MARVIS_AGENT_LOOP_DEFAULT_ENGINE": "developer"}) == "developer"


@pytest.mark.asyncio
async def test_developer_engine_run_turn_uses_readonly_tools(tmp_path) -> None:
    (tmp_path / "sample.py").write_text("def sample():\n    return 'goal-token'\n", encoding="utf-8")
    store = InMemoryRunStore()
    engine = DeveloperExecutionEngine(
        settings=AppSettings(allowed_directories=[str(tmp_path)]),
        store=store,
    )

    state = await engine.start_run("inspect goal-token implementation", "privacy", "developer")
    result = await engine.run_turn(state)

    assert result.finished is True
    assert result.state.phase == RunPhase.COMPLETED
    assert result.state.current_plan["writes_enabled"] is False
    assert result.state.current_plan["allowed_tools"] == list(readonly_developer_tool_names())
    assert {observation.source for observation in result.state.observations} == {
        "dev.git_status",
        "dev.diff_preview",
        "dev.grep",
    }


@pytest.mark.asyncio
async def test_engine_router_resumes_and_cancels_by_run_id(tmp_path) -> None:
    engine = DeveloperExecutionEngine(
        settings=AppSettings(allowed_directories=[str(tmp_path)]),
        store=InMemoryRunStore(),
    )
    router = EngineRouter({"developer": engine}, default_engine="developer")

    state = await router.start_run("inspect repository", engine="developer")
    resumed = await router.resume_run(state.run_id)
    cancelled = await router.cancel_run(state.run_id)

    assert resumed.run_id == state.run_id
    assert cancelled.phase == RunPhase.CANCELLED
