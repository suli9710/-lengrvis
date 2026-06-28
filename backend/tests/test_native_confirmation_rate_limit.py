from __future__ import annotations

import concurrent.futures
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import routes_approvals
from app.core import db
from app.core.schemas import Approval, ApprovalStatus, Plan, PlanStep, RiskLevel, Task, TaskStatus
from app.security.native_confirmation import enforce_native_confirmation_challenge_rate_limit


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    yield


def _pending_approval() -> Approval:
    task = Task(user_goal="rate limit", mode="efficiency", status=TaskStatus.WAITING_USER_APPROVAL)
    db.upsert_model("tasks", task)
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="test.rate_limit",
        description="rate limit",
        args={"path": "a.txt"},
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
        status=TaskStatus.WAITING_USER_APPROVAL,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    db.upsert_model("plans", plan)
    approval = Approval(
        task_id=task.id,
        step_id=step.id,
        message="approve",
        status=ApprovalStatus.PENDING,
        tool_name="test.rate_limit",
        risk_level=RiskLevel.R2_REVERSIBLE_MODIFY.value,
        preview_hmac="preview:test",
    )
    db.upsert_model("approvals", approval, status=approval.status)
    return approval


def test_native_confirmation_challenge_rate_limit_rejects_burst(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "lengrvis.sqlite"
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_MAX", "2")
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_WINDOW_SECONDS", "60")
    scope = "127.0.0.1"
    now = 1_700_000_000.0

    enforce_native_confirmation_challenge_rate_limit(scope, now=now, db_path=db_path)
    enforce_native_confirmation_challenge_rate_limit(scope, now=now + 1, db_path=db_path)

    with pytest.raises(HTTPException) as excinfo:
        enforce_native_confirmation_challenge_rate_limit(scope, now=now + 2, db_path=db_path)

    assert excinfo.value.status_code == 429


def test_native_confirmation_challenge_rate_limit_serializes_concurrent_attempts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "lengrvis.sqlite"
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_MAX", "3")
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_WINDOW_SECONDS", "60")
    scope = "concurrent-client"
    now = 1_700_000_100.0

    def _attempt(offset: float) -> int | None:
        try:
            enforce_native_confirmation_challenge_rate_limit(scope, now=now + offset, db_path=db_path)
        except HTTPException as exc:
            return exc.status_code
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(_attempt, [0.0, 0.01, 0.02, 0.03, 0.04, 0.05]))

    assert results.count(None) == 3
    assert results.count(429) == 3


def test_native_confirmation_challenge_route_enforces_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_MAX", "2")
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_CHALLENGE_RATE_LIMIT_WINDOW_SECONDS", "60")
    approval = _pending_approval()
    app = FastAPI()
    app.include_router(routes_approvals.router, prefix="/api")
    client = TestClient(app)
    payload = {"action": "approve", "expected_preview_hmac": approval.preview_hmac}

    first = client.post(f"/api/approvals/{approval.id}/native-confirmation-challenge", json=payload)
    second = client.post(f"/api/approvals/{approval.id}/native-confirmation-challenge", json=payload)
    third = client.post(f"/api/approvals/{approval.id}/native-confirmation-challenge", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
