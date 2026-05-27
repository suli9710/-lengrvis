from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import Approval, Plan, PlanStep, Task
from app.guardian import create_guardian_app
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.step_phase import StepPhase
from app.orchestration.task_phase import TaskPhase
from app.services import guardian_scheduler, scheduler_service
from app.services.guardian_scheduler import GuardianScheduler
from app.services.scheduler_service import Scheduler, _utc_now


def test_guardian_health_is_lightweight(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    import app.guardian as guardian_module

    def fail_if_called(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("guardian health must not probe local LLMs")

    monkeypatch.setattr(guardian_module, "decode_mobile_token", fail_if_called)

    with TestClient(create_guardian_app()) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "guardian"


def test_import_backend_main_keeps_full_backend_lazy():
    import sys

    sys.modules.pop("app.main", None)
    import backend.main as backend_entry

    assert backend_entry.app.title == "Mavris Guardian Backend"
    assert "app.main" not in sys.modules


def test_guardian_full_backend_probe_uses_runtime_status(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    import app.services.guardian_runtime as guardian_runtime

    requested_urls: list[str] = []

    class FakeResponse:
        status_code = 200

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN002
            return None

        async def get(self, url: str):
            requested_urls.append(url)
            return FakeResponse()

    monkeypatch.setattr(guardian_runtime.httpx, "AsyncClient", FakeClient)

    assert asyncio.run(guardian_runtime.runtime._is_full_backend_healthy()) is True
    assert requested_urls == [f"{guardian_runtime.FULL_BACKEND_URL}/api/runtime/status"]


def test_guardian_idle_recycle_treats_active_runs_as_busy(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()

    import app.services.guardian_runtime as guardian_runtime

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"activeRunIds": ["run_active"]}

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN002
            return None

        async def get(self, url: str):  # noqa: ARG002
            return FakeResponse()

    monkeypatch.setattr(guardian_runtime.httpx, "AsyncClient", FakeClient)

    assert asyncio.run(guardian_runtime.runtime._full_backend_has_active_runs()) is True


def test_guardian_scheduler_creates_wakeup_without_orchestrator(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    scheduler_service._scheduler = None
    guardian_scheduler._scheduler = None

    def fail_if_orchestrator_imported(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("guardian scheduler must not execute orchestrator work")

    monkeypatch.setattr("app.agents.orchestrator_agent.OrchestratorAgent", fail_if_orchestrator_imported)

    schedule = Scheduler().schedule("*/5 * * * *", "scan downloads", mode="hybrid")
    guardian = GuardianScheduler()

    async def runner():
        return await guardian.tick(now=_utc_now() + timedelta(days=1))

    fired = asyncio.run(runner())

    assert schedule.id in fired
    refreshed = Scheduler().get(schedule.id)
    assert refreshed is not None
    assert refreshed.last_status == "waiting_user_confirmation"
    wakeups = db.fetch_many("wakeups", "source_id = ?", (schedule.id,), limit=10)
    assert len(wakeups) == 1
    assert wakeups[0]["goal"] == "scan downloads"
    assert wakeups[0]["status"] == "pending"


def test_guardian_approval_does_not_execute_step(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    task = Task(
        id="task_guardian_approval",
        user_goal="approve later",
        status=TaskPhase.EXECUTION,
        phase=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    step = PlanStep(
        id="step_guardian_approval",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.read",
        description="Read file",
        status="waiting_user_approval",
        step_phase=StepPhase.TOOL_REVIEW,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve read")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval, status=approval.status)

    import app.api.routes_guardian as routes_guardian

    async def fake_wake_full_backend(_approval):
        return None

    monkeypatch.setattr(routes_guardian, "_wake_full_backend_for_approval", fake_wake_full_backend)

    with TestClient(create_guardian_app()) as client:
        response = client.post(f"/api/approvals/{approval.id}/approve")

    assert response.status_code == 200
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"
    refreshed_plan = Plan.model_validate(db.fetch_one("plans", plan.id))
    assert refreshed_plan.steps[0].status == "waiting_user_approval"
