"""R6/R7 delegation metadata and run hint wiring."""

from __future__ import annotations

import pytest

from app.agents.delegation_metadata import (
    DEVELOPER_ENGINE_DISCLOSURE,
    build_task_delegation_metadata,
    developer_engine_capabilities,
    infer_supervisor_agent_hint,
    merge_run_task_metadata,
    plan_matches_supervisor_hint,
    plan_tools_outside_visible,
)
from app.agents.worker_agents import normalize_supervisor_agent_hint


def test_normalize_supervisor_agent_hint_rejects_orchestrator():
    assert normalize_supervisor_agent_hint("OrchestratorAgent") == ""
    assert normalize_supervisor_agent_hint("BrowserAgent") == "BrowserAgent"


def test_infer_supervisor_agent_hint_browser_and_search():
    assert infer_supervisor_agent_hint("帮我读取 https://example.com 网页正文") == "BrowserAgent"
    assert infer_supervisor_agent_hint("搜索一下最新的 AI 新闻") == "SearchAgent"
    assert infer_supervisor_agent_hint("帮我检查这台电脑") == "ComputerAgent"


def test_infer_supervisor_agent_hint_file_organize_and_duplicates():
    assert infer_supervisor_agent_hint("帮我整理这批发票文件") == "FileAgent"
    assert infer_supervisor_agent_hint("帮我查找重复文件") == "FileAgent"


def test_merge_run_task_metadata_prefers_explicit_hint():
    merged = merge_run_task_metadata(agent_hint="SearchAgent", goal="搜索 AI 新闻")
    assert merged["supervisor_agent_hint"] == "SearchAgent"


def test_merge_run_task_metadata_infers_when_missing():
    merged = merge_run_task_metadata(goal="帮我读取 https://example.com 网页正文")
    assert merged["supervisor_agent_hint"] == "BrowserAgent"


def test_developer_engine_capabilities_discloses_read_only():
    caps = developer_engine_capabilities(writes_enabled=False)
    assert caps["writes_enabled"] is False
    assert caps["mode"] == "read_only_code_analysis"
    assert "只读" in caps["disclosure"]
    assert caps["disclosure"] == DEVELOPER_ENGINE_DISCLOSURE
    assert "developer_writes_enabled" in caps["disclosure"]


def test_os_engine_capabilities_write_routed_disclosure():
    from app.agents.delegation_metadata import os_engine_capabilities

    caps = os_engine_capabilities(route_rule="developer_write_os")
    assert caps["writes_enabled"] is False
    assert "developer_writes_enabled" in caps["disclosure"]
    assert os_engine_capabilities(route_rule="os_goal") == {"writes_enabled": False, "mode": "os_execution"}


def test_developer_engine_capabilities_discloses_controlled_writes():
    caps = developer_engine_capabilities(writes_enabled=True)
    assert caps["writes_enabled"] is True
    assert caps["mode"] == "controlled_code_editing"
    assert "workspace" in caps["disclosure"]
    assert "pytest" in caps["disclosure"]


def test_build_task_delegation_metadata_strips_unknown():
    assert build_task_delegation_metadata(agent_hint="EvilAgent") == {}
    assert build_task_delegation_metadata(extra={"supervisor_agent_hint": "EvilAgent"}) == {}
    assert build_task_delegation_metadata(
        agent_hint="SearchAgent",
        extra={"supervisor_agent_hint": "BrowserAgent"},
    ) == {"supervisor_agent_hint": "SearchAgent"}


@pytest.mark.anyio
async def test_create_run_persists_supervisor_hint_on_os_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    monkeypatch.setattr("app.services.run_service.schedule_background", lambda coro, **kwargs: None)
    monkeypatch.setattr("app.services.run_service.track_active_run", lambda run_id, task: None)
    monkeypatch.setattr("app.services.run_service._track_run_router", lambda run_id, router: None)

    from app.core import db
    from app.core.schemas import RunEngine
    from app.services import run_service

    db.init_db()
    run = await run_service.create_run(
        "搜索一下最新的 AI 新闻",
        "efficiency",
        RunEngine.AUTO,
        agent_hint="SearchAgent",
    )
    assert run.task_id
    task = db.fetch_one("tasks", run.task_id)
    assert (task or {}).get("metadata", {}).get("supervisor_agent_hint") == "SearchAgent"
    caps = run_service.engine_capabilities_for_run(run)
    assert caps.get("supervisor_agent_hint") == "SearchAgent"


def test_plan_matches_supervisor_hint():
    from app.core.schemas import Plan, PlanStep, RiskLevel

    plan = Plan(
        task_id="t1",
        goal="g",
        steps=[
            PlanStep(
                task_id="t1",
                agent_name="BrowserAgent",
                tool_name="browser.read_page",
                description="read",
                risk_level=RiskLevel.R0_READ_ONLY,
            )
        ],
    )
    assert plan_matches_supervisor_hint(plan, "BrowserAgent", ["browser.read_page"])
    assert not plan_matches_supervisor_hint(plan, "BrowserAgent", ["file.search_by_name"])


def test_plan_tools_outside_visible():
    from app.core.schemas import Plan, PlanStep, RiskLevel

    plan = Plan(
        task_id="t1",
        goal="g",
        steps=[
            PlanStep(
                task_id="t1",
                agent_name="FileAgent",
                tool_name="file.search_by_name",
                description="search",
                risk_level=RiskLevel.R0_READ_ONLY,
            )
        ],
    )
    assert plan_tools_outside_visible(plan, ["browser.read_page"]) == ["file.search_by_name"]


@pytest.mark.anyio
async def test_runs_api_accepts_agent_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    monkeypatch.setattr("app.services.run_service.schedule_background", lambda coro, **kwargs: None)
    monkeypatch.setattr("app.services.run_service.track_active_run", lambda run_id, task: None)
    monkeypatch.setattr("app.services.run_service._track_run_router", lambda run_id, router: None)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes_runs import router as runs_router
    from app.core import db

    db.init_db()
    app = FastAPI()
    app.include_router(runs_router, prefix="/api")
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "message": "搜索一下最新的 AI 新闻",
                "mode": "efficiency",
                "engine": "auto",
                "agent_hint": "SearchAgent",
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["engine_capabilities"]["supervisor_agent_hint"] == "SearchAgent"
        assert payload["engine_route_rule"] == "ambiguous_fallback"
        assert payload["engine_capabilities"]["route_rule"] == "ambiguous_fallback"
        from app.services import run_service

        run = run_service.get_run(payload["run_id"])
        task = db.fetch_one("tasks", run.task_id)
        assert task is not None
        assert task["metadata"]["supervisor_agent_hint"] == "SearchAgent"


@pytest.mark.anyio
async def test_create_run_developer_engine_capabilities(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.delenv("LENGRVIS_DEVELOPER_WRITES_ENABLED", raising=False)
    def _discard_background(coro, **kwargs):  # noqa: ANN001, ARG001
        coro.close()

    monkeypatch.setattr("app.services.run_service.schedule_background", _discard_background)
    monkeypatch.setattr("app.services.run_service.track_active_run", lambda run_id, task: None)
    monkeypatch.setattr("app.services.run_service._track_run_router", lambda run_id, router: None)

    from app.core import db
    from app.core.schemas import RunEngine
    from app.services import run_service

    db.init_db()
    run = await run_service.create_run("inspect repository structure", "efficiency", RunEngine.DEVELOPER)
    caps = run_service.engine_capabilities_for_run(run)
    assert caps["writes_enabled"] is False
    assert "只读" in caps["disclosure"]
    assert run_service.engine_route_rule_for_run(run) == "explicit_override"
    assert caps["route_rule"] == "explicit_override"


@pytest.mark.anyio
async def test_runs_api_exposes_engine_route_rule_for_chinese_os_goal(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path / "workspace"))
    (tmp_path / "workspace").mkdir()
    monkeypatch.setattr("app.services.run_service.schedule_background", lambda coro, **kwargs: None)
    monkeypatch.setattr("app.services.run_service.track_active_run", lambda run_id, task: None)
    monkeypatch.setattr("app.services.run_service._track_run_router", lambda run_id, router: None)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.routes_runs import router as runs_router
    from app.core import db

    db.init_db()
    app = FastAPI()
    app.include_router(runs_router, prefix="/api")
    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={"message": "卸载微信", "mode": "efficiency", "engine": "auto"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["engine_route_rule"] == "os_goal"
        assert payload["engine_capabilities"]["route_rule"] == "os_goal"

        detail = client.get(f"/api/runs/{payload['run_id']}")
        assert detail.status_code == 200
        assert detail.json()["engine_route_rule"] == "os_goal"
