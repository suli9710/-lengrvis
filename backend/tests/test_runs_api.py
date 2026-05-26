from __future__ import annotations

import concurrent.futures
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_approvals import router as approvals_router
from app.api.routes_runs import router, ws_router
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, Plan, PlanStep
from app.agents.planner_agent import PlannerAgent
from app.orchestration.run_event_bus import RunEventBus
from app.orchestration.execution_models import EngineTurnResult, RunPhase as EngineRunPhase
from app.services import run_service
from app.policy.risk import RiskLevel
from app.services.mobile_pairing_service import approve_approval
from app.tools.registry import register_all_tools
from app.tools.schemas import ToolDefinition


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(approvals_router, prefix="/api")
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


def test_approval_resume_continues_remaining_run_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    target = tmp_path / "approved-multi-step.txt"
    target.write_text("remove me", encoding="utf-8")
    db.init_db()

    async def spy_create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ARG001
        approval_step = PlanStep(
            id="approval_step",
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
        follow_up = PlanStep(
            id="follow_up_step",
            task_id=task_id,
            order=2,
            agent_name="ComputerAgent",
            tool_name="system.get_info",
            description="Inspect system after approval.",
            args={},
            expected_observation="system.get_info completed.",
            risk_level=RiskLevel.R0_READ_ONLY,
            depends_on=[approval_step.id],
        )
        return Plan(
            task_id=task_id,
            goal=goal,
            steps=[approval_step, follow_up],
            global_risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
            requires_user_approval=True,
        )

    monkeypatch.setattr(PlannerAgent, "create_plan", spy_create_plan)

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "delete approved file then inspect system", "mode": "efficiency", "engine": "os"},
        ).json()
        _wait_for_phase(client, created["run_id"], "awaiting_approval")
        approval = Approval.model_validate(db.fetch_many("approvals", limit=10)[0])
        approved = client.post(f"/api/approvals/{approval.id}/approve")
        assert approved.status_code == 200
        final = _wait_for_phase(client, created["run_id"], "completed", "failed", "denied")
        assert final["phase"] == "completed"
        plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=1)[0])
        follow_up = next(step for step in plan.steps if step.id == "follow_up_step")
        assert follow_up.status == "succeeded"


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


def test_reject_approval_moves_run_to_cancelled(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    target = tmp_path / "reject-delete.txt"
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
            json={"message": "delete rejected file", "mode": "efficiency", "engine": "os"},
        ).json()
        _wait_for_phase(client, created["run_id"], "awaiting_approval")
        approval = Approval.model_validate(db.fetch_many("approvals", limit=10)[0])

        rejected = client.post(f"/api/approvals/{approval.id}/reject")

        assert rejected.status_code == 200
        final = _wait_for_phase(client, created["run_id"], "cancelled")
        assert final["phase"] == "cancelled"
        assert target.exists()
        plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=1)[0])
        assert plan.steps[0].status == "denied"
        timeline = client.get(f"/api/runs/{created['run_id']}/timeline").json()
        names = [event["name"] for event in timeline["events"]]
        assert "run.cancelled" in names


def test_cancel_run_expires_pending_approval_and_blocks_late_approve(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    target = tmp_path / "late-approve-delete.txt"
    target.write_text("keep me", encoding="utf-8")
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
            json={"message": "delete then cancel", "mode": "efficiency", "engine": "os"},
        ).json()
        _wait_for_phase(client, created["run_id"], "awaiting_approval")
        approval = Approval.model_validate(db.fetch_many("approvals", limit=10)[0])

        cancelled = client.post(f"/api/runs/{created['run_id']}/cancel")
        late_approve = client.post(f"/api/approvals/{approval.id}/approve")

        assert cancelled.status_code == 200
        assert late_approve.status_code == 409
        assert target.exists()
        refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
        assert refreshed.status == ApprovalStatus.EXPIRED
        assert refreshed.consumed_at is None


def test_sync_resume_schedules_background_without_event_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "inspect repository", "mode": "privacy", "engine": "developer"},
        ).json()
        _wait_for_phase(client, created["run_id"], "completed", "failed")
        run = run_service.get_run(created["run_id"])
        run.phase = run_service.RunPhase.PAUSED
        db.upsert_model("runs", run)

        response = client.post(f"/api/runs/{created['run_id']}/resume")

        assert response.status_code == 200
        assert response.json()["phase"] == "running"
        _wait_for_phase(client, created["run_id"], "completed", "failed")


def test_run_event_publish_allocates_contiguous_sequences_concurrently(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    bus = RunEventBus()
    run_id = "run_concurrent_events"

    def publish_one(index: int) -> int:
        return bus.publish(run_id, "tool.progress", {"index": index}).sequence

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        sequences = list(executor.map(publish_one, range(80)))

    events = db.fetch_run_events(run_id, limit=200)
    stored_sequences = [event["sequence"] for event in events]
    assert len(events) == 80
    assert sorted(sequences) == list(range(1, 81))
    assert stored_sequences == list(range(1, 81))


def test_os_run_denies_r4_tool_without_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MARVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_API_KEY", "")
    calls: list[dict] = []

    forbidden_tool = ToolDefinition(
        name="test.r4_forbidden",
        description="forbidden run API test tool",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={},
        risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
        agent_owner="ComputerAgent",
        supports_dry_run=False,
        requires_authorized_path=False,
        execute=lambda args, context: calls.append(dict(args)) or {"ok": True},  # noqa: ARG005
        effects=["write"],
        resource_kinds=["system"],
        fast_path_eligible=True,
    )
    register_all_tools(extra_definitions=[forbidden_tool], load_skills=False)
    db.init_db()

    async def spy_create_plan(self, task_id, goal, mode, tools, **kwargs):  # noqa: ARG001
        return Plan(
            task_id=task_id,
            goal=goal,
            steps=[
                PlanStep(
                    task_id=task_id,
                    order=1,
                    agent_name="ComputerAgent",
                    tool_name="test.r4_forbidden",
                    description="Attempt forbidden tool.",
                    args={},
                    risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                )
            ],
            global_risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
        )

    monkeypatch.setattr(PlannerAgent, "create_plan", spy_create_plan)

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "attempt forbidden tool", "mode": "efficiency", "engine": "os"},
        )
        assert created.status_code == 200
        final = _wait_for_phase(client, created.json()["run_id"], "denied", "failed")
        assert final["phase"] == "denied"
        timeline = client.get(f"/api/runs/{created.json()['run_id']}/timeline").json()
        names = [event["name"] for event in timeline["events"]]
        assert "run.denied" in names
        assert calls == []


def test_cancelled_run_is_not_overwritten_by_finishing_engine_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    started = threading.Event()
    release = threading.Event()
    original_router_factory = run_service._engine_router

    class BlockingRouter:
        max_turns = 1

        async def start_run(self, goal, mode, engine):  # noqa: ANN001, ANN202
            return await original_router_factory(run_service.get_effective_settings()).start_run(goal, mode, engine)

        async def run_turn(self, state):  # noqa: ANN001, ANN202
            started.set()
            assert release.wait(timeout=5)
            finished = state.model_copy(
                update={
                    "phase": EngineRunPhase.COMPLETED,
                    "turn_count": state.turn_count + 1,
                    "transition_reason": "completed after cancel",
                },
                deep=True,
            )
            return EngineTurnResult(state=finished, finished=True, message="completed after cancel")

    monkeypatch.setattr(run_service, "_engine_router", lambda settings: BlockingRouter())

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "inspect repository", "mode": "privacy", "engine": "developer"},
        ).json()
        assert started.wait(timeout=5)
        cancelled = client.post(f"/api/runs/{created['run_id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["phase"] == "cancelled"
        release.set()
        time.sleep(0.3)
        final = client.get(f"/api/runs/{created['run_id']}").json()
        timeline = client.get(f"/api/runs/{created['run_id']}/timeline").json()
        names = [event["name"] for event in timeline["events"]]
        assert final["phase"] == "cancelled"
        assert "run.cancelled" in names
        assert "run.completed" not in names


def test_paused_run_is_not_overwritten_by_finishing_engine_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    started = threading.Event()
    release = threading.Event()
    original_router_factory = run_service._engine_router

    class BlockingRouter:
        max_turns = 1

        async def start_run(self, goal, mode, engine):  # noqa: ANN001, ANN202
            return await original_router_factory(run_service.get_effective_settings()).start_run(goal, mode, engine)

        async def run_turn(self, state):  # noqa: ANN001, ANN202
            started.set()
            assert release.wait(timeout=5)
            finished = state.model_copy(
                update={
                    "phase": EngineRunPhase.COMPLETED,
                    "turn_count": state.turn_count + 1,
                    "transition_reason": "completed after pause",
                },
                deep=True,
            )
            return EngineTurnResult(state=finished, finished=True, message="completed after pause")

    monkeypatch.setattr(run_service, "_engine_router", lambda settings: BlockingRouter())

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "inspect repository", "mode": "privacy", "engine": "developer"},
        ).json()
        assert started.wait(timeout=5)
        paused = client.post(f"/api/runs/{created['run_id']}/pause")
        assert paused.status_code == 200
        assert paused.json()["phase"] == "paused"
        release.set()
        time.sleep(0.3)
        final = client.get(f"/api/runs/{created['run_id']}").json()
        timeline = client.get(f"/api/runs/{created['run_id']}/timeline").json()
        names = [event["name"] for event in timeline["events"]]
        assert final["phase"] == "paused"
        assert "run.completed" not in names
