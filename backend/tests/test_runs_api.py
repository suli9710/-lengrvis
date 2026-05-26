from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_runs import router, ws_router
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, Plan, PlanStep
from app.agents.planner_agent import PlannerAgent
from app.policy.risk import RiskLevel
from app.services.mobile_pairing_service import approve_approval


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(ws_router)
    app.include_router(ws_router, prefix="/api")
    return app


def _wait_for_phase(client: TestClient, run_id: str, *phases: str) -> dict:
    for _ in range(80):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["phase"] in phases:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not reach {phases}")


def test_run_api_routes_developer_engine_and_replays_events(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()
    app = _test_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/runs",
            json={"message": "inspect repository git status", "mode": "privacy", "engine": "developer"},
        )
        assert created.status_code == 200
        run = created.json()
        assert run["engine"] == "developer"
        final = _wait_for_phase(client, run["run_id"], "completed", "failed")
        assert final["phase"] == "completed"

        timeline = client.get(f"/api/runs/{run['run_id']}/timeline").json()
        event_names = [event["name"] for event in timeline["events"]]
        assert "run.started" in event_names
        assert "turn.started" in event_names
        assert "run.completed" in event_names

        with client.websocket_connect(f"/ws/runs/{run['run_id']}") as websocket:
            assert websocket.receive_json()["type"] == "connected"
            replayed = []
            while True:
                event = websocket.receive_json()
                if event["type"] == "replay.completed":
                    break
                replayed.append(event)
        assert any(event.get("event") == "run.started" for event in replayed)


def test_auto_routing_selects_developer_for_code_goal(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "fix failing pytest in backend", "mode": "privacy", "engine": "auto"},
        )
        assert created.status_code == 200
        assert created.json()["engine"] == "developer"


def test_os_run_keeps_r2_dry_run_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    target = tmp_path / "delete-me.txt"
    target.write_text("remove me", encoding="utf-8")
    db.init_db()

    async def spy_create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ARG001
        step = PlanStep(
            task_id=task_id,
            order=1,
            agent_name="FileAgent",
            tool_name="file.trash",
            description="Move file to trash after approval.",
            args={"path": str(target)},
            expected_observation="file.trash completed.",
            risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_approval=True,
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            assumptions=["deterministic approval test"],
            steps=[step],
            global_risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_user_approval=True,
        )

    monkeypatch.setattr(PlannerAgent, "create_plan", spy_create_plan)

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "delete the temp file", "mode": "efficiency", "engine": "os"},
        )
        assert created.status_code == 200
        run = created.json()
        assert run["engine"] == "os"
        final = _wait_for_phase(client, run["run_id"], "awaiting_approval", "failed", "denied")
        assert final["phase"] == "awaiting_approval"
        approvals = db.fetch_many("approvals", limit=10)
        assert approvals and approvals[0]["status"] == "pending"
        assert target.exists(), "R2 dry-run must not delete before approval."


def test_run_timeline_reconciles_after_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    target = tmp_path / "approved-delete.txt"
    target.write_text("remove me", encoding="utf-8")
    db.init_db()

    async def spy_create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ARG001
        return Plan(
            task_id=task_id,
            goal=goal,
            steps=[
                PlanStep(
                    task_id=task_id,
                    order=1,
                    agent_name="FileAgent",
                    tool_name="file.trash",
                    description="Move file to trash after approval.",
                    args={"path": str(target)},
                    expected_observation="file.trash completed.",
                    risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
                    requires_approval=True,
                )
            ],
            global_risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_user_approval=True,
        )

    monkeypatch.setattr(PlannerAgent, "create_plan", spy_create_plan)

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "delete approved file", "mode": "efficiency", "engine": "os"},
        ).json()
        final = _wait_for_phase(client, created["run_id"], "awaiting_approval")
        approvals = db.fetch_many("approvals", limit=10)
        approval = Approval.model_validate(approvals[0])
        approve_approval(approval.id)
        approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
        assert approval.status == ApprovalStatus.APPROVED

        from app.api.routes_approvals import _execute_approved_step
        import asyncio

        asyncio.run(_execute_approved_step(approval))
        after = _wait_for_phase(client, created["run_id"], "completed", "failed")
        assert after["phase"] == "completed"
        timeline = client.get(f"/api/runs/{created['run_id']}/timeline").json()
        names = [event["name"] for event in timeline["events"]]
        assert "run.waiting_approval" in names
        assert "run.completed" in names


def test_resume_does_not_bypass_waiting_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    target = tmp_path / "resume-delete.txt"
    target.write_text("remove me", encoding="utf-8")
    db.init_db()

    async def spy_create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ARG001
        return Plan(
            task_id=task_id,
            goal=goal,
            steps=[
                PlanStep(
                    task_id=task_id,
                    order=1,
                    agent_name="FileAgent",
                    tool_name="file.trash",
                    description="Move file to trash after approval.",
                    args={"path": str(target)},
                    expected_observation="file.trash completed.",
                    risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
                    requires_approval=True,
                )
            ],
            global_risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_user_approval=True,
        )

    monkeypatch.setattr(PlannerAgent, "create_plan", spy_create_plan)

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "delete needs approval", "mode": "efficiency", "engine": "os"},
        ).json()
        before = _wait_for_phase(client, created["run_id"], "awaiting_approval")
        resumed = client.post(f"/api/runs/{created['run_id']}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["phase"] == "awaiting_approval"
        time.sleep(0.2)
        after = client.get(f"/api/runs/{created['run_id']}").json()
        assert after["phase"] == before["phase"]
        assert target.exists(), "Resume must not execute an unapproved R2 step."
