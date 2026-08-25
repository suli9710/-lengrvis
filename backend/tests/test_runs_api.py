from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import json
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from native_confirmation_helpers import native_confirmation_headers

from app.agents.planner_agent import PlannerAgent
from app.api.routes_approvals import router as approvals_router
from app.api.routes_perception import router as perception_router
from app.api.routes_runs import router, ws_router
from app.core import db
from app.core.schemas import (
    AgentMessage,
    Approval,
    ApprovalStatus,
    MessageType,
    Plan,
    PlanStep,
    SafetyReview,
    Task,
    ToolCall,
    ToolResult,
)
from app.orchestration.execution_models import EngineTurnResult
from app.orchestration.execution_models import RunPhase as EngineRunPhase
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.run_event_bus import RunEventBus, task_message_to_run_event
from app.orchestration.task_phase import TaskPhase
from app.policy.risk import RiskLevel
from app.services import run_service
from app.services.mobile_pairing_service import approve_approval
from app.tools.registry import register_all_tools
from app.tools.schemas import ToolDefinition


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.include_router(approvals_router, prefix="/api")
    app.include_router(perception_router, prefix="/api")
    app.include_router(ws_router)
    app.include_router(ws_router, prefix="/api")
    return app


def _wait_for_phase(
    client: TestClient,
    run_id: str,
    *phases: str,
    timeout_seconds: float = 15.0,
    poll_interval_seconds: float = 0.05,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    payload: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["phase"] in phases:
            if payload["phase"] in {"completed", "failed", "denied", "cancelled"}:
                _wait_for_run_inactive(run_id)
            return payload
        time.sleep(poll_interval_seconds)
    raise AssertionError(
        f"Run {run_id} did not reach {phases} within {timeout_seconds:.1f}s; "
        f"last_phase={payload.get('phase')!r}; active_run_ids={run_service.active_run_ids()}; "
        f"recent_events={_recent_run_events(client, run_id)}"
    )


def _wait_for_run_inactive(run_id: str, *, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if run_id not in run_service.active_run_ids():
            return
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} was still active after reaching a terminal/waiting phase")


def _wait_for_executable_approval(task_id: str, *, timeout_seconds: float = 15.0) -> Approval:
    deadline = time.monotonic() + timeout_seconds
    last_state: dict = {}
    while time.monotonic() < deadline:
        approvals = db.fetch_many("approvals", "task_id = ? AND status = ?", (task_id, "pending"), limit=10)
        if approvals:
            approval = Approval.model_validate(approvals[0])
            task_data = db.fetch_one("tasks", approval.task_id) or {}
            plans = db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=1)
            plan = plans[0] if plans else {}
            step = next((item for item in plan.get("steps", []) if item.get("id") == approval.step_id), {})
            last_state = {
                "approval_id": approval.id,
                "task_execution_stage": task_data.get("execution_stage"),
                "step_status": step.get("status"),
            }
            if (
                task_data.get("execution_stage") == ExecutionStage.AWAITING_APPROVAL.value
                and step.get("status") == "waiting_user_approval"
            ):
                return approval
        time.sleep(0.05)
    raise AssertionError(f"Task {task_id} did not expose an executable approval; last_state={last_state!r}")


def _recent_run_events(client: TestClient, run_id: str) -> list[dict]:
    try:
        response = client.get(f"/api/runs/{run_id}/timeline")
        if response.status_code != 200:
            return [{"timeline_status": response.status_code}]
        timeline = response.json()
    except Exception as exc:  # noqa: BLE001 - diagnostics should not mask the wait failure.
        return [{"timeline_error": f"{type(exc).__name__}: {exc}"}]
    recent = []
    for event in (timeline.get("events") or [])[-8:]:
        payload = event.get("payload") if isinstance(event, dict) else {}
        recent.append(
            {
                "name": event.get("name") if isinstance(event, dict) else None,
                "payload_keys": sorted(payload) if isinstance(payload, dict) else [],
            }
        )
    return recent


def _fake_send2trash(path: str) -> None:
    target = Path(path)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)


def test_run_api_routes_developer_engine_and_replays_events(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    fake_cli = tmp_path / "fake_lengrvis_success.py"
    fake_cli.write_text(
        """
from __future__ import annotations

import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--print", action="store_true")
parser.add_argument("--output-format")
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--bare", action="store_true")
parser.add_argument("--model")
parser.add_argument("--max-turns")
parser.add_argument("--add-dir")
parser.add_argument("--permission-mode")
parser.add_argument("--disallowedTools")
parser.add_argument("--allowedTools")
parser.add_argument("prompt")
args = parser.parse_args()

print(
    json.dumps({"type": "system", "subtype": "init", "tools": args.allowedTools.split(",")}),
    flush=True,
)
print(
    json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "API fake developer run"},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "README.md"}},
                ],
            },
        }
    ),
    flush=True,
)
print(
    json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "API fake complete"}),
    flush=True,
)
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_CODE_COMMAND", f'"{sys.executable}" -u "{fake_cli}"')
    monkeypatch.setenv("LENGRVIS_API_KEY", "test-api-key")
    monkeypatch.setenv("LENGRVIS_MODEL", "openai/gpt-5")
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
        final = _wait_for_phase(client, run["run_id"], "completed", "failed", timeout_seconds=45.0)
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


def test_run_timeline_progress_and_wire_redact_secrets_and_internal_paths(monkeypatch, tmp_path):
    # SEC-001 regression: the run-engine read/stream surfaces must (1) drop the
    # internal ``state._runtime`` block (which otherwise leaks an absolute local
    # path) and (2) redact secrets in event payloads / run message, while still
    # preserving 32-hex identifiers and the structured payload shape the desktop
    # client and the other contract tests depend on.
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    runtime_path = "C:\\Users\\Operator\\AppData\\Lengrvis\\data-dir-secret"
    run_id = "osrun_" + "abcdef0123456789abcdef0123456789"
    task_id = "task_" + "0123456789abcdef0123456789abcdef"
    run = run_service.Run(
        id=run_id,
        message="summarize the report token=run-message-secret-1234567890",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        task_id=task_id,
        state={
            "run_id": run_id,
            "engine": "os",
            "phase": "running",
            "goal": "summarize the report",
            "mode": "efficiency",
            "task_id": task_id,
            "_runtime": {"data_dir": runtime_path},
        },
    )
    db.upsert_model("runs", run)
    bus = run_service.run_event_bus
    bus.publish(
        run_id,
        "tool.proposed",
        {
            "tool_name": "file.read_text",
            "step_id": "step_1",
            "structured_payload": {
                "api_key": "sk-" + "proposedapikeyvalue1234567890",
                "password": "hunter2-" + "proposed-secret",
                "note": "Authorization: Bearer proposedbearertoken1234567890",
            },
        },
    )
    bus.publish(
        run_id,
        "tool.progress",
        {
            "tool_name": "file.read_text",
            "status": "running",
            "structured_payload": {
                "tool_name": "file.read_text",
                "status": "running",
                "api_key": "sk-progressapikeyvalue1234567890",
            },
        },
    )

    with TestClient(_test_app()) as client:
        timeline = client.get(f"/api/runs/{run_id}/timeline").json()
        progress = client.get(f"/api/runs/{run_id}/progress").json()
        state = client.get(f"/api/runs/{run_id}").json()
        with client.websocket_connect(f"/ws/runs/{run_id}") as websocket:
            assert websocket.receive_json()["type"] == "connected"
            replayed = []
            while True:
                event = websocket.receive_json()
                if event["type"] == "replay.completed":
                    break
                replayed.append(event)

    timeline_text = json.dumps(timeline, ensure_ascii=False)
    progress_text = json.dumps(progress, ensure_ascii=False)
    state_text = json.dumps(state, ensure_ascii=False)
    wire_text = json.dumps(replayed, ensure_ascii=False)

    # Internal runtime metadata and the absolute data dir path are stripped.
    assert "_runtime" not in timeline["run"]["state"]
    assert "data_dir" not in str(timeline["run"]["state"])
    assert runtime_path not in timeline_text
    assert runtime_path not in progress_text
    assert runtime_path not in wire_text

    # Secrets in event payloads and the run message are redacted everywhere.
    for secret in (
        "sk-" + "proposedapikeyvalue1234567890",
        "sk-progressapikeyvalue1234567890",
        "hunter2-" + "proposed-secret",
        "proposedbearertoken1234567890",
    ):
        assert secret not in timeline_text, secret
        assert secret not in wire_text, secret
    assert "sk-progressapikeyvalue1234567890" not in progress_text
    assert "run-message-secret-1234567890" not in timeline_text
    assert "run-message-secret-1234567890" not in state_text

    # Identifiers and structured payload shape are preserved (desktop contract).
    assert timeline["run"]["id"] == run_id
    assert timeline["run"]["task_id"] == task_id
    assert progress["run_id"] == run_id
    assert progress["task_id"] == task_id
    proposed = next(event for event in timeline["events"] if event["name"] == "tool.proposed")
    assert proposed["payload"]["tool_name"] == "file.read_text"
    assert proposed["payload"]["step_id"] == "step_1"
    assert proposed["payload"]["structured_payload"]["note"] == "Authorization: Bearer [REDACTED]"
    assert any(event.get("event") == "tool.proposed" for event in replayed)
    assert progress["progress"][-1]["payload"]["status"] == "running"


def test_run_state_exposes_linked_task_result_contract_without_forging_unlinked_results(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()

    def store_task_run(
        *,
        suffix: str,
        task_status: TaskPhase,
        run_phase: run_service.RunPhase,
        final_summary: str = "",
        tool_result: bool = False,
        tool_review: bool = False,
        final_review: bool = False,
        blocking_review: bool = False,
    ) -> tuple[str, str]:
        task = Task(
            id=f"task_result_contract_{suffix}",
            user_goal=f"review result contract {suffix}",
            mode="efficiency",
            status=task_status,
            final_summary=final_summary,
        )
        db.upsert_model("tasks", task)
        if tool_result:
            call = ToolCall(
                id=f"tool_result_contract_{suffix}",
                task_id=task.id,
                step_id=f"step_result_contract_{suffix}",
                tool_name="system.diagnostics",
                risk_level=RiskLevel.R0_READ_ONLY,
            )
            db.upsert_model("tool_calls", call)
            db.upsert_model(
                "tool_results",
                ToolResult(
                    id=f"result_contract_{suffix}",
                    tool_call_id=call.id,
                    ok=True,
                    output={"summary": "raw output C:\\Users\\Suli\\secret.txt token=secret-run-result"},
                ),
            )
            if tool_review:
                db.upsert_model(
                    "safety_reviews",
                    SafetyReview(
                        id=f"tool_review_contract_{suffix}",
                        task_id=task.id,
                        step_id=call.step_id,
                        target_type="tool_result",
                        verdict="allow",
                        risk_level=RiskLevel.R0_READ_ONLY,
                    ),
                )
        if final_review:
            db.upsert_model(
                "safety_reviews",
                SafetyReview(
                    id=f"final_review_contract_{suffix}",
                    task_id=task.id,
                    target_type="final",
                    verdict="allow",
                    risk_level=RiskLevel.R0_READ_ONLY,
                ),
            )
        if blocking_review:
            db.upsert_model(
                "safety_reviews",
                SafetyReview(
                    id=f"blocking_review_contract_{suffix}",
                    task_id=task.id,
                    target_type="final",
                    verdict="deny",
                    risk_level=RiskLevel.R2_REVERSIBLE_MODIFY,
                    reasons=["raw reason C:\\Users\\Suli\\secret.txt token=blocked-secret"],
                ),
            )
        run = run_service.Run(
            id=f"osrun_result_contract_{suffix}",
            message=f"run result contract {suffix} token=run-message-secret",
            mode=task.mode,
            requested_engine=run_service.RunEngine.OS,
            engine=run_service.RunEngine.OS,
            phase=run_phase,
            task_id=task.id,
        )
        db.upsert_model("runs", run)
        return run.id, task.id

    verified_run_id, _ = store_task_run(
        suffix="verified",
        task_status=TaskPhase.COMPLETED,
        run_phase=run_service.RunPhase.COMPLETED,
        final_summary="Diagnostics result verified.",
        tool_result=True,
        tool_review=True,
        final_review=True,
    )
    progress_run_id, _ = store_task_run(
        suffix="progress",
        task_status=TaskPhase.COMPLETED,
        run_phase=run_service.RunPhase.COMPLETED,
        final_summary="Diagnostics result visible.",
        tool_result=True,
    )
    evidence_only_run_id, _ = store_task_run(
        suffix="created",
        task_status=TaskPhase.CREATED,
        run_phase=run_service.RunPhase.CREATED,
    )
    safe_failure_run_id, _ = store_task_run(
        suffix="failure",
        task_status=TaskPhase.FAILED,
        run_phase=run_service.RunPhase.FAILED,
        final_summary="safe failure",
        blocking_review=True,
    )
    unlinked = run_service.Run(
        id="osrun_result_contract_unlinked",
        message="completed run without task",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.COMPLETED,
    )
    db.upsert_model("runs", unlinked)

    with TestClient(_test_app()) as client:
        verified = client.get(f"/api/runs/{verified_run_id}").json()
        progress = client.get(f"/api/runs/{progress_run_id}").json()
        evidence_only = client.get(f"/api/runs/{evidence_only_run_id}").json()
        safe_failure = client.get(f"/api/runs/{safe_failure_run_id}").json()
        unlinked_payload = client.get(f"/api/runs/{unlinked.id}").json()

    assert verified["completion_evidence"]["level"] == "completed_result"
    assert verified["completion_evidence"]["result_verified"] is True
    assert verified["result_quality"]["state"] == "verified_result"
    assert verified["result_quality"]["can_treat_as_done"] is True

    assert progress["completion_evidence"]["level"] == "visible_progress"
    assert progress["completion_evidence"]["result_verified"] is False
    assert progress["result_quality"]["state"] == "visible_progress"
    assert progress["result_quality"]["can_treat_as_done"] is False

    assert evidence_only["completion_evidence"]["level"] == "task_created"
    assert evidence_only["result_quality"]["state"] == "task_evidence_only"
    assert evidence_only["result_quality"]["can_treat_as_done"] is False

    assert safe_failure["completion_evidence"]["level"] == "safe_failure"
    assert safe_failure["result_quality"]["state"] == "safe_failure"
    assert safe_failure["result_quality"]["can_treat_as_done"] is False

    assert unlinked_payload["phase"] == "completed"
    assert unlinked_payload["completion_evidence"]["result_verified"] is False
    assert unlinked_payload["result_quality"]["can_treat_as_done"] is False

    dumped = json.dumps([verified, progress, evidence_only, safe_failure, unlinked_payload], ensure_ascii=False)
    assert "C:\\Users\\Suli" not in dumped
    assert "secret-run-result" not in dumped
    assert "blocked-secret" not in dumped
    assert "run-message-secret" not in dumped


def test_task_message_to_run_event_uses_safe_projection_for_persisted_payload():
    local_path = r"C:\Users\Suli\Desktop\mavris\.env"
    secret = "sk-run-event-message-secret-value"
    message = AgentMessage(
        task_id="task_run_event_projection",
        from_agent="PlannerAgent",
        message_type=MessageType.PROPOSAL,
        content=f"Read {local_path} token={secret}",
        structured_payload={"path": local_path, "api_key": secret},
        metadata={"error": f"failed at {local_path} token={secret}"},
        tool_calls=[
            {
                "id": "call_run_event",
                "type": "function",
                "function": {"name": "file.read_text", "arguments": {"path": local_path, "api_key": secret}},
            }
        ],
    )

    translated = task_message_to_run_event(message, run_id="run_event_projection")

    assert translated is not None
    name, payload = translated
    dumped = json.dumps(payload, ensure_ascii=False)
    assert name == "tool.proposed"
    assert local_path not in dumped
    assert secret not in dumped
    assert payload["content"] == "Read [REDACTED_LOCAL_PATH] token=[REDACTED]"
    assert payload["structured_payload"]["path"] == "[REDACTED_LOCAL_PATH]"
    assert payload["structured_payload"]["api_key"] == "***"
    assert payload["metadata"]["structured_payload"]["api_key"] == "***"
    arguments = json.loads(payload["tool_calls"][0]["function"]["arguments"])
    assert arguments["path"] == "[REDACTED_LOCAL_PATH]"
    assert arguments["api_key"] == "***"
    assert payload["tool_calls"][0]["id"] == "call_run_event"


def test_auto_routing_uses_os_for_write_intent_code_goal(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DEVELOPER_WRITES_ENABLED", raising=False)
    db.init_db()
    scheduled = []

    def schedule_spy(coro, *, data_dir=None):  # noqa: ANN001, ANN202, ARG001
        scheduled.append(coro)
        coro.close()

        class Done:
            def done(self) -> bool:
                return True

        return Done()

    monkeypatch.setattr(run_service, "_schedule_background", schedule_spy)

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "fix failing pytest in backend", "mode": "privacy", "engine": "auto"},
        )
        assert created.status_code == 200
        assert created.json()["engine"] == "os"
    # create_run now schedules a single wrapper coroutine that sets up the
    # message bridge and drives the engine loop on the resident loop.
    assert len(scheduled) == 1
    assert scheduled[0].__name__ == "_start_engine_loop"


def test_auto_routing_uses_developer_when_writes_enabled_for_pytest_fix_goal(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DEVELOPER_WRITES_ENABLED", "true")
    db.init_db()
    scheduled = []

    def schedule_spy(coro, *, data_dir=None):  # noqa: ANN001, ANN202, ARG001
        scheduled.append(coro)
        coro.close()

        class Done:
            def done(self) -> bool:
                return True

        return Done()

    monkeypatch.setattr(run_service, "_schedule_background", schedule_spy)

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "fix failing pytest in backend", "mode": "privacy", "engine": "auto"},
        )
        assert created.status_code == 200
        assert created.json()["engine"] == "developer"
    assert len(scheduled) == 1


def test_developer_writes_enabled_run_requires_backend_approval_before_launch(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DEVELOPER_WRITES_ENABLED", "true")
    record_path = tmp_path / "lengrvis_argv.json"
    fake_cli = tmp_path / "fake_lengrvis_writes.py"
    fake_cli.write_text(
        """
from __future__ import annotations

import argparse
import json
import os
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--print", action="store_true")
parser.add_argument("--output-format")
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--bare", action="store_true")
parser.add_argument("--model")
parser.add_argument("--max-turns")
parser.add_argument("--add-dir")
parser.add_argument("--permission-mode")
parser.add_argument("--disallowedTools")
parser.add_argument("--allowedTools")
parser.add_argument("prompt")
args = parser.parse_args()

record_path = os.environ.get("LENGRVIS_FAKE_RECORD")
if record_path:
    with open(record_path, "w", encoding="utf-8") as fh:
        json.dump({"argv": sys.argv[1:], "prompt": args.prompt}, fh)

print(json.dumps({"type": "system", "subtype": "init", "tools": args.allowedTools.split(",")}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "Approved developer run executed"},
]}}), flush=True)
print(json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "Approved developer run completed",
}), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_CODE_COMMAND", f'"{sys.executable}" -u "{fake_cli}"')
    monkeypatch.setenv("LENGRVIS_FAKE_RECORD", str(record_path))
    monkeypatch.setenv("LENGRVIS_API_KEY", "test-api-key")
    monkeypatch.setenv("LENGRVIS_MODEL", "openai/gpt-5")
    db.init_db()
    app = _test_app()

    with TestClient(app) as client:
        created = client.post(
            "/api/runs",
            json={"message": "fix failing pytest in backend/tests", "mode": "privacy", "engine": "developer"},
        )
        assert created.status_code == 200
        assert created.json()["engine"] == "developer"
        final = _wait_for_phase(client, created.json()["run_id"], "awaiting_approval", "completed", "failed")
        assert final["phase"] == "awaiting_approval"
        assert not record_path.exists(), "developer subprocess must not launch before backend approval"

        timeline = client.get(f"/api/runs/{created.json()['run_id']}/timeline").json()
        task_id = timeline["run"]["task_id"]
        approvals = db.fetch_many("approvals", "task_id = ?", (task_id,), limit=10)
        assert approvals and approvals[0]["status"] == ApprovalStatus.PENDING
        approval = Approval.model_validate(approvals[0])
        assert approval.tool_name == "developer.lengrvis_code"
        assert approval.risk_level == RiskLevel.R2_REVERSIBLE_MODIFY.value
        assert "write" in approval.tool_effects
        assert approval.dry_run_summary

        approved = client.post(
            f"/api/approvals/{approval.id}/approve",
            headers=native_confirmation_headers("approve", approval.id),
        )
        assert approved.status_code == 200
        assert approved.json()["execution_scheduled"] is True
        completed = _wait_for_phase(client, created.json()["run_id"], "completed", "failed", timeout_seconds=20)
        assert completed["phase"] == "completed"

    record = json.loads(record_path.read_text(encoding="utf-8"))
    argv = record["argv"]
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Write" in allowed and "Edit" in allowed
    assert argv[argv.index("--permission-mode") + 1] == "default"
    assert "skip-permissions" not in " ".join(argv)
    assert "Write/Edit tools are enabled" in record["prompt"]
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    assert refreshed_approval.consumed_at


@pytest.mark.parametrize(
    "message",
    [
        "帮我检查这台电脑",
        "run system diagnostics",
        "run computer diagnostics",
    ],
)
def test_run_api_system_diagnostics_stays_os_local_only(monkeypatch, tmp_path, message):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_DEFAULT_ENGINE", "developer")
    db.init_db()

    provider_calls: list[str] = []

    def fail_provider(*args, **kwargs):  # noqa: ANN001, ANN202, ARG001
        provider_calls.append("planner_provider")
        raise AssertionError("system diagnostics must use the deterministic local-only plan.")

    monkeypatch.setattr("app.agents.planner_agent.get_provider", fail_provider)

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": message, "mode": "privacy", "engine": "auto"},
        )
        assert created.status_code == 200
        run = created.json()
        assert run["engine"] == "os"
        final = _wait_for_phase(client, run["run_id"], "completed", "failed", "denied")
        assert final["phase"] == "completed"

        timeline = client.get(f"/api/runs/{run['run_id']}/timeline").json()
        progress = client.get(f"/api/runs/{run['run_id']}/progress").json()

    assert provider_calls == []
    assert timeline["run"]["requested_engine"] == "auto"
    assert timeline["run"]["engine"] == "os"
    assert "read-only system diagnostics" in timeline["events"][0]["payload"]["transition_reason"]

    plan_events = [
        event
        for event in timeline["events"]
        if event["name"] == "plan.generated" and event["payload"].get("structured_payload", {}).get("steps")
    ]
    assert plan_events
    step = plan_events[-1]["payload"]["structured_payload"]["steps"][0]
    assert step["tool_name"] == "system.diagnostics"
    assert step["risk_level"] == RiskLevel.R0_READ_ONLY.value
    assert step["requires_approval"] is False
    assert db.fetch_many("approvals", "task_id = ?", (timeline["run"]["task_id"],), limit=10) == []

    engine_event_outputs = [
        event
        for event in timeline["events"]
        if event["name"] == "tool.result" and event["payload"].get("tool_name") == "events"
    ]
    assert engine_event_outputs
    os_events = engine_event_outputs[-1]["payload"]["output"]
    diagnostics_result = next(
        event
        for event in os_events
        if event.get("event") == "tool.result"
        and event.get("tool_result", {}).get("output", {}).get("local_ai", {}).get("scope") == "local_only"
    )
    diagnostics = diagnostics_result["tool_result"]["output"]

    step_outcome_events = [
        event
        for event in timeline["events"]
        if event["name"] == "tool.result" and event["payload"].get("tool_name") == "step_outcomes"
    ]
    assert step_outcome_events
    step_outcome = next(
        outcome
        for outcome in step_outcome_events[-1]["payload"]["output"]
        if outcome["tool_name"] == "system.diagnostics"
    )
    assert step_outcome["kind"] == "succeeded"
    assert diagnostics["local_ai"]["scope"] == "local_only"
    assert {"info", "disks", "network", "battery", "top_processes", "suggestions"}.issubset(diagnostics)

    tool = register_all_tools(load_skills=False).get("system.diagnostics")
    assert tool.risk_level == RiskLevel.R0_READ_ONLY
    assert tool.is_read_only() is True
    assert tool.external_network is False
    assert tool.effects == ["read", "inspect"]
    assert tool.resource_kinds == ["system"]

    assert progress["engine"] == "os"
    assert progress["phase"] == "completed"
    assert any(
        event["payload"].get("structured_payload", {}).get("tool_name") == "system.diagnostics"
        and event["payload"].get("structured_payload", {}).get("status") == "completed"
        for event in progress["progress"]
    )


def test_run_start_failure_redacts_error_in_state_and_timeline(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()

    class Router:
        max_turns = 1

        async def start_run(self, goal, mode, engine, *, task_metadata=None):  # noqa: ANN001, ANN202, ARG002
            raise RuntimeError("provider failed token=run-start-secret-1234567890")

    monkeypatch.setattr(run_service, "_engine_router", lambda settings: Router())

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "start failure", "mode": "efficiency", "engine": "os"},
        )
        state = client.get(f"/api/runs/{created.json()['run_id']}")
        timeline = client.get(f"/api/runs/{created.json()['run_id']}/timeline")

    payload_text = json.dumps([created.json(), state.json(), timeline.json()], ensure_ascii=False)
    assert created.status_code == 200
    assert state.status_code == 200
    assert timeline.status_code == 200
    assert created.json()["phase"] == "failed"
    assert state.json()["phase"] == "failed"
    assert "provider failed" in state.json()["error"]
    assert "run-start-secret-1234567890" not in payload_text
    assert "[REDACTED]" in payload_text


def test_os_run_keeps_r2_dry_run_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
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
        timeline = client.get(f"/api/runs/{run['run_id']}/timeline").json()
        plan_events = [
            event
            for event in timeline["events"]
            if event["name"] == "plan.generated" and event["payload"].get("structured_payload", {}).get("steps")
        ]
        assert plan_events
        latest_step = plan_events[-1]["payload"]["structured_payload"]["steps"][0]
        assert latest_step["risk_level"] == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM.value
        assert latest_step["tool_effects"]


def test_run_timeline_reconciles_after_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr("app.tools.file_tools.send2trash", _fake_send2trash)
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
        awaiting = _wait_for_phase(client, created["run_id"], "awaiting_approval")
        approval = _wait_for_executable_approval(awaiting["task_id"])
        approve_approval(approval.id)
        approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
        assert approval.status == ApprovalStatus.APPROVED

        from app.api.routes_approvals import _execute_approved_step

        asyncio.run(_execute_approved_step(approval))
        after = _wait_for_phase(client, created["run_id"], "completed", "failed")
        assert after["phase"] == "completed"
        timeline = client.get(f"/api/runs/{created['run_id']}/timeline").json()
        names = [event["name"] for event in timeline["events"]]
        assert "run.waiting_approval" in names
        assert "run.completed" in names


def test_approval_resume_continues_remaining_run_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setattr("app.tools.file_tools.send2trash", _fake_send2trash)
    target = tmp_path / "approved-multi-step.txt"
    target.write_text("remove me", encoding="utf-8")
    keep_file = tmp_path / "keep-after-trash.txt"
    keep_file.write_text("still here", encoding="utf-8")
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
            agent_name="FileAgent",
            tool_name="file.read_text",
            description="Read remaining file after approval.",
            args={"path": str(keep_file)},
            expected_observation="file.read_text completed.",
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
            json={"message": "delete approved file then read the remaining file", "mode": "efficiency", "engine": "os"},
        ).json()
        _wait_for_phase(client, created["run_id"], "awaiting_approval")
        approval = Approval.model_validate(db.fetch_many("approvals", limit=10)[0])
        approved = client.post(
            f"/api/approvals/{approval.id}/approve",
            headers=native_confirmation_headers("approve", approval.id),
        )
        assert approved.status_code == 200
        final = _wait_for_phase(client, created["run_id"], "completed", "failed", "denied")
        assert final["phase"] == "completed"
        plans = db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=5)
        plan = Plan.model_validate(plans[0])
        follow_up = next((step for step in plan.steps if step.id == "follow_up_step"), None)
        assert follow_up is not None, f"plan steps={[(s.id, s.status) for s in plan.steps]}"
        assert follow_up.status == "succeeded"


def test_run_state_runtime_metadata_does_not_break_resume(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    run = run_service.Run(
        id="osrun_runtime_metadata",
        message="resume",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.PAUSED,
        state={
            "run_id": "osrun_runtime_metadata",
            "engine": "os",
            "phase": "paused",
            "turn_count": 0,
            "goal": "resume",
            "mode": "efficiency",
            "_runtime": {"data_dir": str(tmp_path / "data")},
        },
    )
    db.upsert_model("runs", run)

    state = run_service._state_from_run(run)
    state = state.model_copy(update={"phase": EngineRunPhase.RUNNING}, deep=True)
    updated = run_service._update_run_from_state(run, state)

    assert updated.state["_runtime"]["data_dir"] == str(tmp_path / "data")
    assert updated.state["schema_version"] == run_service.CURRENT_RUN_STATE_SCHEMA_VERSION
    assert run_service._state_from_run(updated).phase == EngineRunPhase.RUNNING


def test_run_trace_context_is_stable_and_inherits_request_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    from app.observability.tracing import span

    run = run_service.Run(
        id="osrun_trace_context",
        message="trace context",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.CREATED,
        state={},
    )

    with span("request") as request_span:
        first = run_service._ensure_run_trace_context(run)
    second = run_service._ensure_run_trace_context(run)

    assert first == second
    assert first["trace_id"] == request_span.trace_id
    assert first["parent_span_id"] == request_span.span_id
    assert run.state["_runtime"]["observability"] == first


@pytest.mark.parametrize("schema_version", [1, 2])
def test_persisted_run_state_migrates_supported_checkpoint_versions(monkeypatch, tmp_path, schema_version):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    run_id = f"osrun_checkpoint_v{schema_version}"
    persisted = {
        "schema_version": schema_version,
        "run_id": run_id,
        "engine": "os",
        "phase": "paused",
        "goal": "resume versioned checkpoint",
        "mode": "efficiency",
    }
    if schema_version >= 2:
        persisted["route_rule"] = "ambiguous_fallback"
    run = run_service.Run(
        id=run_id,
        message="resume versioned checkpoint",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.PAUSED,
        state=persisted,
    )

    state = run_service._state_from_run(run)
    serialized = run_service._state_payload_for_run(run, state)
    round_tripped = run_service._parse_persisted_run_state(run)

    assert state.schema_version == run_service.CURRENT_RUN_STATE_SCHEMA_VERSION
    assert run.state["schema_version"] == run_service.CURRENT_RUN_STATE_SCHEMA_VERSION
    assert serialized["schema_version"] == run_service.CURRENT_RUN_STATE_SCHEMA_VERSION
    assert round_tripped == state
    assert state.route_rule == "ambiguous_fallback"
    assert state.continuation_kind == ""


def test_unversioned_legacy_run_state_migrates_and_serializes_current_version(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    run = run_service.Run(
        id="osrun_legacy_checkpoint",
        message="resume legacy checkpoint",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.PAUSED,
        state={
            "run_id": "osrun_legacy_checkpoint",
            "engine": "os",
            "phase": "paused",
            "goal": "resume legacy checkpoint",
            "mode": "efficiency",
        },
    )

    state = run_service._state_from_run(run)
    serialized = run_service._state_payload_for_run(run, state)

    assert state.schema_version == run_service.CURRENT_RUN_STATE_SCHEMA_VERSION
    assert run.state["schema_version"] == run_service.CURRENT_RUN_STATE_SCHEMA_VERSION
    assert serialized["schema_version"] == run_service.CURRENT_RUN_STATE_SCHEMA_VERSION


@pytest.mark.parametrize("schema_version", [0, run_service.CURRENT_RUN_STATE_SCHEMA_VERSION + 1])
def test_resume_with_unsupported_run_state_schema_fails_closed(monkeypatch, tmp_path, schema_version):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    scheduled: list[object] = []
    run_id = f"osrun_unsupported_checkpoint_{schema_version}"
    run = run_service.Run(
        id=run_id,
        message="resume unsupported checkpoint",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.PAUSED,
        state={
            "schema_version": schema_version,
            "run_id": run_id,
            "engine": "os",
            "phase": "paused",
            "goal": "resume unsupported checkpoint",
            "mode": "efficiency",
        },
    )
    db.upsert_model("runs", run)
    monkeypatch.setattr(run_service, "_engine_router", lambda settings: object())
    monkeypatch.setattr(
        run_service,
        "_schedule_background",
        lambda coro, *, data_dir=None: scheduled.append(coro),  # noqa: ARG005
    )

    resumed = run_service._schedule_resume(run)

    assert resumed.phase == run_service.RunPhase.FAILED
    assert scheduled == []
    assert "schema_version" in resumed.error
    assert run_service.get_run(run.id).phase == run_service.RunPhase.FAILED


def test_resume_with_invalid_persisted_state_fails_run_without_scheduling(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    scheduled: list[object] = []
    run = run_service.Run(
        id="osrun_invalid_resume_state",
        message="resume invalid state",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.PAUSED,
        state={
            "run_id": "osrun_invalid_resume_state",
            "engine": "not-a-real-engine",
            "phase": "paused",
            "goal": "resume invalid state",
            "mode": "efficiency",
        },
    )
    db.upsert_model("runs", run)
    monkeypatch.setattr(run_service, "_engine_router", lambda settings: object())
    monkeypatch.setattr(
        run_service,
        "_schedule_background",
        lambda coro, *, data_dir=None: scheduled.append(coro),  # noqa: ARG005
    )

    resumed = run_service._schedule_resume(run)

    assert resumed.phase == run_service.RunPhase.FAILED
    assert scheduled == []
    assert run_service.get_run(run.id).phase == run_service.RunPhase.FAILED
    events = run_service.list_run_events(run.id)
    assert any(event.name == "run.failed" for event in events)


def test_resume_with_non_mapping_persisted_state_fails_run_without_scheduling(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    scheduled: list[object] = []
    run = run_service.Run(
        id="osrun_shape_invalid_resume_state",
        message="resume invalid shape",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.PAUSED,
        state={
            "run_id": "osrun_shape_invalid_resume_state",
            "engine": "os",
            "phase": "paused",
            "goal": "resume invalid shape",
            "mode": "efficiency",
        },
    )
    run.state = "not-a-state-object"  # type: ignore[assignment]
    monkeypatch.setattr(run_service, "_engine_router", lambda settings: object())
    monkeypatch.setattr(
        run_service,
        "_schedule_background",
        lambda coro, *, data_dir=None: scheduled.append(coro),  # noqa: ARG005
    )

    resumed = run_service._schedule_resume(run)

    assert resumed.phase == run_service.RunPhase.FAILED
    assert scheduled == []
    assert run_service.get_run(run.id).phase == run_service.RunPhase.FAILED


def test_invalid_approval_continuation_state_returns_false(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    run = run_service.Run(
        id="osrun_invalid_continuation",
        message="invalid continuation",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        state={
            "run_id": "osrun_invalid_continuation",
            "engine": "not-a-real-engine",
            "phase": "running",
        },
    )

    assert run_service._is_approval_continuation(run) is False


def test_non_mapping_persisted_state_helpers_are_tolerated(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    run = run_service.Run(
        id="osrun_invalid_state_shape_helpers",
        message="invalid helper state",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        state={
            "run_id": "osrun_invalid_state_shape_helpers",
            "engine": "os",
            "phase": "running",
            "goal": "invalid helper state",
            "mode": "efficiency",
        },
    )
    run.state = "not-a-state-object"  # type: ignore[assignment]

    assert run_service._is_approval_continuation(run) is False
    run_service._sync_persisted_state_phase(run, run_service.RunPhase.PAUSED, "shape_error")
    run_service._cancel_persisted_state(run)
    assert run.state == "not-a-state-object"


def test_invalid_historical_agent_message_is_skipped():
    assert run_service._agent_message({"id": "msg_invalid"}) is None


def test_invalid_historical_plan_row_is_skipped(monkeypatch):
    monkeypatch.setattr(run_service.db, "fetch_many", lambda *args, **kwargs: [{"id": "plan_invalid"}])

    assert run_service._latest_plan_for_task("task_invalid_plan") is None


def test_run_state_runtime_errors_are_not_swallowed(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    run = run_service.Run(
        id="osrun_state_runtime_error",
        message="resume runtime bug",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.PAUSED,
        state={
            "run_id": "osrun_state_runtime_error",
            "engine": "os",
            "phase": "paused",
            "goal": "resume runtime bug",
            "mode": "efficiency",
        },
    )

    def raise_runtime(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("runstate parser bug")

    monkeypatch.setattr(run_service, "_engine_router", lambda settings: object())
    monkeypatch.setattr(run_service.RunState, "model_validate", raise_runtime)

    with pytest.raises(RuntimeError, match="runstate parser bug"):
        run_service._schedule_resume(run)


def test_agent_message_runtime_errors_are_not_swallowed(monkeypatch):
    def raise_runtime(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("agent message parser bug")

    monkeypatch.setattr(AgentMessage, "model_validate", raise_runtime)

    with pytest.raises(RuntimeError, match="agent message parser bug"):
        run_service._agent_message({"id": "msg_runtime_bug"})


def test_plan_runtime_errors_are_not_swallowed(monkeypatch):
    monkeypatch.setattr(run_service.db, "fetch_many", lambda *args, **kwargs: [{"id": "plan_runtime_bug"}])

    def raise_runtime(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("plan parser bug")

    monkeypatch.setattr(run_service.Plan, "model_validate", raise_runtime)

    with pytest.raises(RuntimeError, match="plan parser bug"):
        run_service._latest_plan_for_task("task_runtime_bug")


def test_task_lookup_store_errors_are_tolerated(monkeypatch):
    run = run_service.Run(
        id="run_task_lookup_store_error",
        message="store error",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        task_id="task_store_error",
    )

    def raise_sqlite_error(task_id):  # noqa: ARG001
        raise sqlite3.Error("task db unavailable")

    monkeypatch.setattr(run_service, "get_task", raise_sqlite_error)

    assert run_service._sync_run_phase_from_task(run) is run


def test_latest_plan_fetch_json_errors_are_tolerated(monkeypatch):
    def raise_json_error(*args, **kwargs):  # noqa: ARG001
        raise json.JSONDecodeError("bad plan json", "", 0)

    monkeypatch.setattr(run_service.db, "fetch_many", raise_json_error)

    assert run_service._latest_plan_for_task("task_bad_plan_json") is None


def test_pause_updates_persisted_run_state_phase(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(user_goal="pause persisted state", mode="efficiency", status=TaskPhase.EXECUTION)
    db.upsert_model("tasks", task)
    run = run_service.Run(
        id="osrun_pause_state",
        message=task.user_goal,
        mode=task.mode,
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        task_id=task.id,
        state={
            "run_id": "osrun_pause_state",
            "engine": "os",
            "phase": "running",
            "goal": task.user_goal,
            "mode": task.mode,
            "task_id": task.id,
        },
    )
    db.upsert_model("runs", run)

    paused = run_service.pause_run(run.id)

    assert paused.phase == run_service.RunPhase.PAUSED
    assert paused.state["phase"] == "paused"
    assert run_service._state_from_run(paused).phase == EngineRunPhase.PAUSED


def test_pause_run_cancels_active_engine_work(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    run = run_service.Run(
        id="osrun_pause_cancels_active",
        message="pause active engine",
        mode="efficiency",
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        state={"run_id": "osrun_pause_cancels_active", "engine": "os", "phase": "running"},
    )
    db.upsert_model("runs", run)
    active = concurrent.futures.Future()
    run_service._track_active_run(run.id, active)

    try:
        paused = run_service.pause_run(run.id)

        assert paused.phase == run_service.RunPhase.PAUSED
        assert active.cancelled()
    finally:
        run_service._untrack_active_run(run.id)


def test_get_run_syncs_waiting_approval_from_task_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(user_goal="needs approval", mode="efficiency", status=TaskPhase.EXECUTION)
    task.execution_stage = ExecutionStage.AWAITING_APPROVAL
    db.upsert_model("tasks", task)
    run = run_service.Run(
        id="osrun_stale_waiting_read",
        message=task.user_goal,
        mode=task.mode,
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        task_id=task.id,
        state={
            "run_id": "osrun_stale_waiting_read",
            "engine": "os",
            "phase": "running",
            "goal": task.user_goal,
            "mode": task.mode,
            "task_id": task.id,
        },
    )
    db.upsert_model("runs", run)

    synced = run_service.get_run(run.id)

    assert synced.phase == run_service.RunPhase.AWAITING_APPROVAL
    assert synced.state["phase"] == "awaiting_approval"


def test_get_run_syncs_cancelled_task_with_cancelled_event(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(
        user_goal="cancel from task state",
        mode="efficiency",
        status=TaskPhase.CANCELLED,
        final_summary="cancelled by user",
    )
    db.upsert_model("tasks", task)
    run = run_service.Run(
        id="osrun_stale_cancelled_read",
        message=task.user_goal,
        mode=task.mode,
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        task_id=task.id,
        state={
            "run_id": "osrun_stale_cancelled_read",
            "engine": "os",
            "phase": "running",
            "goal": task.user_goal,
            "mode": task.mode,
            "task_id": task.id,
        },
    )
    db.upsert_model("runs", run)

    synced = run_service.get_run(run.id)
    run_service.get_run(run.id)

    events = run_service.list_run_events(run.id)
    cancelled_events = [event for event in events if event.name == "run.cancelled"]
    assert synced.phase == run_service.RunPhase.CANCELLED
    assert synced.state["phase"] == "cancelled"
    assert len(cancelled_events) == 1
    assert cancelled_events[0].payload["reason"] == "task_status_sync"


def test_get_run_syncs_denied_task_with_denied_event(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(
        user_goal="blocked by safety",
        mode="efficiency",
        status=TaskPhase.DENIED,
        final_summary="Denied: policy blocked this task.",
    )
    db.upsert_model("tasks", task)
    run = run_service.Run(
        id="osrun_stale_denied_read",
        message=task.user_goal,
        mode=task.mode,
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        task_id=task.id,
        state={
            "run_id": "osrun_stale_denied_read",
            "engine": "os",
            "phase": "running",
            "goal": task.user_goal,
            "mode": task.mode,
            "task_id": task.id,
        },
    )
    db.upsert_model("runs", run)

    synced = run_service.get_run(run.id)
    run_service.get_run(run.id)

    events = run_service.list_run_events(run.id)
    denied_events = [event for event in events if event.name == "run.denied"]
    assert synced.phase == run_service.RunPhase.DENIED
    assert synced.state["phase"] == "denied"
    assert len(denied_events) == 1
    assert denied_events[0].payload["reason"] == "task_status_sync"


def test_pause_run_expires_pending_approval_and_denies_waiting_step(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(user_goal="pause approval", mode="efficiency", status=TaskPhase.EXECUTION)
    task.execution_stage = ExecutionStage.AWAITING_APPROVAL
    db.upsert_model("tasks", task)
    step = PlanStep(
        id="pause_waiting_step",
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.trash",
        description="Waiting for approval.",
        status="waiting_user_approval",
        requires_approval=True,
    )
    plan = Plan(task_id=task.id, goal=task.user_goal, steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve pause test")
    run = run_service.Run(
        id="osrun_pause_approval",
        message=task.user_goal,
        mode=task.mode,
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.AWAITING_APPROVAL,
        task_id=task.id,
        state={
            "run_id": "osrun_pause_approval",
            "engine": "os",
            "phase": "awaiting_approval",
            "goal": task.user_goal,
            "mode": task.mode,
            "task_id": task.id,
        },
    )
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval)
    db.upsert_model("runs", run)

    paused = run_service.pause_run(run.id)

    assert paused.phase == run_service.RunPhase.PAUSED
    refreshed_approval = Approval.model_validate(db.fetch_one("approvals", approval.id))
    refreshed_plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (task.id,), limit=1)[0])
    assert refreshed_approval.status == ApprovalStatus.EXPIRED
    assert refreshed_approval.consumed_at is None
    assert refreshed_plan.steps[0].status == "denied"


def test_active_running_run_is_not_synced_back_to_paused_task_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    task = Task(user_goal="resume should stay running", mode="efficiency", status=TaskPhase.EXECUTION)
    task.execution_stage = ExecutionStage.PAUSED
    db.upsert_model("tasks", task)
    run = run_service.Run(
        id="osrun_active_resume_not_paused",
        message=task.user_goal,
        mode=task.mode,
        requested_engine=run_service.RunEngine.OS,
        engine=run_service.RunEngine.OS,
        phase=run_service.RunPhase.RUNNING,
        task_id=task.id,
        state={
            "run_id": "osrun_active_resume_not_paused",
            "engine": "os",
            "phase": "running",
            "goal": task.user_goal,
            "mode": task.mode,
            "task_id": task.id,
        },
    )
    db.upsert_model("runs", run)

    class ActiveFuture:
        def done(self) -> bool:
            return False

    run_service.track_active_run(run.id, ActiveFuture())
    try:
        synced = run_service.get_run(run.id)
    finally:
        run_service.untrack_active_run(run.id)

    assert synced.phase == run_service.RunPhase.RUNNING
    assert synced.state["phase"] == "running"


def test_resume_does_not_bypass_waiting_approval(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
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

        rejected = client.post(
            f"/api/approvals/{approval.id}/reject",
            headers=native_confirmation_headers("reject", approval.id),
        )

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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
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
        late_approve = client.post(
            f"/api/approvals/{approval.id}/approve",
            headers=native_confirmation_headers("approve", approval.id),
        )

        assert cancelled.status_code == 200
        assert late_approve.status_code == 409
        assert target.exists()
        refreshed = Approval.model_validate(db.fetch_one("approvals", approval.id))
        refreshed_plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (approval.task_id,), limit=1)[0])
        assert refreshed.status == ApprovalStatus.EXPIRED
        assert refreshed.consumed_at is None
        assert refreshed_plan.steps[0].status == "denied"


def test_pause_and_cancel_are_noops_for_terminal_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    run = run_service.Run(
        id="run_terminal_idempotent",
        message="already done",
        mode="efficiency",
        requested_engine=run_service.RunEngine.DEVELOPER,
        engine=run_service.RunEngine.DEVELOPER,
        phase=run_service.RunPhase.COMPLETED,
        state={
            "run_id": "run_terminal_idempotent",
            "engine": "developer",
            "phase": "completed",
            "goal": "already done",
            "mode": "efficiency",
        },
    )
    db.upsert_model("runs", run)
    scheduled = []

    def schedule_spy(coro, *, data_dir=None):  # noqa: ANN001, ANN202, ARG001
        scheduled.append(coro)
        coro.close()
        raise AssertionError("terminal run cancellation must not schedule background cleanup")

    monkeypatch.setattr(run_service, "_schedule_background", schedule_spy)

    paused = run_service.pause_run(run.id)
    cancelled = run_service.cancel_run(run.id)

    assert paused.phase == run_service.RunPhase.COMPLETED
    assert cancelled.phase == run_service.RunPhase.COMPLETED
    assert scheduled == []
    assert run_service.get_run(run.id).phase == run_service.RunPhase.COMPLETED


def test_sync_resume_schedules_background_without_event_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    db.init_db()
    run = run_service.Run(
        id="devrun_sync_resume",
        message="inspect repository",
        mode="privacy",
        requested_engine=run_service.RunEngine.DEVELOPER,
        engine=run_service.RunEngine.DEVELOPER,
        phase=run_service.RunPhase.PAUSED,
        state={
            "run_id": "devrun_sync_resume",
            "engine": "developer",
            "phase": "paused",
            "goal": "inspect repository",
            "mode": "privacy",
        },
    )
    db.upsert_model("runs", run)

    with TestClient(_test_app()) as client:
        scheduled = []

        def schedule_spy(coro, *, data_dir=None):  # noqa: ANN001, ANN202, ARG001
            scheduled.append(coro)
            coro.close()

            class Pending:
                def done(self) -> bool:
                    return False

                def cancel(self) -> bool:
                    return True

            return Pending()

        monkeypatch.setattr(run_service, "_schedule_background", schedule_spy)

        response = client.post(f"/api/runs/{run.id}/resume")

        assert response.status_code == 200
        assert response.json()["phase"] == "running"
        assert len(scheduled) == 1
        persisted = db.fetch_one("runs", run.id)
        assert persisted is not None
        assert persisted["state"]["schema_version"] == run_service.CURRENT_RUN_STATE_SCHEMA_VERSION


def test_perception_suggestion_launch_creates_run_without_direct_tool_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    started = []
    scheduled = []

    class Router:
        max_turns = 1

    from app.orchestration.execution_models import RunState
    from app.perception.intent_predictor import IntentSuggestion
    from app.services import perception_suggestion_service

    async def start_run(self, goal, mode, engine, *, task_metadata=None):  # noqa: ANN001, ANN202, ARG002
        started.append({"goal": goal, "mode": mode, "engine": engine})
        return RunState(
            run_id="osrun_suggestion_launch",
            engine="os",
            phase=EngineRunPhase.RUNNING,
            goal=goal,
            mode=mode,
        )

    async def run_turn(self, state):  # noqa: ANN001, ANN202
        raise AssertionError("suggestion launch should not execute a turn inline")

    Router.start_run = start_run
    Router.run_turn = run_turn
    monkeypatch.setattr(run_service, "_engine_router", lambda settings: Router())

    def schedule_spy(coro, *, data_dir=None):  # noqa: ANN001, ANN202, ARG001
        scheduled.append(coro)
        coro.close()

        class Done:
            def done(self) -> bool:
                return False

        return Done()

    monkeypatch.setattr(run_service, "_schedule_background", schedule_spy)
    monkeypatch.setattr(
        perception_suggestion_service,
        "get_suggestion",
        lambda suggestion_id: IntentSuggestion(
            id=suggestion_id,
            title="Open app",
            prompt="Open Notepad with app.launch_installed.",
            confidence=0.95,
            agent_hint="AppAgent",
            reason="Visible app context suggests launching Notepad.",
        ),
    )

    with TestClient(_test_app()) as client:
        response = client.post(
            "/api/perception/suggestions/open_notepad/launch",
            json={"mode": "efficiency", "engine": "os"},
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == "osrun_suggestion_launch"
    assert started and "Open Notepad" in started[0]["goal"]
    assert db.fetch_many("tool_calls", limit=10) == []
    assert db.fetch_many("tool_results", limit=10) == []
    assert len(scheduled) == 1


def test_perception_capture_api_returns_sanitized_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    from app.perception import context_store
    from app.perception.schemas import AppContext

    context_store.clear()
    monkeypatch.setattr(
        "app.perception.screen_monitor.capture_screen_frame",
        lambda **kwargs: type(
            "Frame",
            (),
            {
                "image_base64": base64.b64encode(b"raw-screenshot").decode("ascii"),
                "timestamp": "2026-05-26T00:00:00+00:00",
                "width": 640,
                "height": 360,
                "original_width": 640,
                "original_height": 360,
            },
        )(),
    )
    monkeypatch.setattr(
        "app.perception.screen_monitor.describe_image",
        lambda args, context: {
            "ok": True,
            "description": "A document editor is visible.",
            "tags": ["document"],
            "structured_labels": {"ocr_full_text": "raw OCR should not leak"},
        },
    )
    monkeypatch.setattr(
        "app.perception.screen_monitor.get_current_app_context",
        lambda: AppContext(available=True, process_name="WINWORD.EXE", active_window_title="Report.docx"),
    )

    with TestClient(_test_app()) as client:
        response = client.post("/api/perception/capture")

    assert response.status_code == 200
    payload = response.json()
    dumped = str(payload)
    assert "image_base64" not in dumped
    assert "raw-screenshot" not in dumped
    assert "raw OCR should not leak" not in dumped
    assert payload["screen_state"]["description"] == "A document editor is visible."


def test_legacy_proactive_suggestions_route_uses_perception_service(monkeypatch):
    from app.api import routes_chat
    from app.perception.intent_predictor import IntentSuggestion

    monkeypatch.setattr(
        routes_chat.perception_suggestion_service,
        "current_suggestions",
        lambda: [
            IntentSuggestion(
                id="from_store",
                title="Stored suggestion",
                prompt="Launch through the perception suggestion store.",
                confidence=0.91,
            )
        ],
    )
    app = FastAPI()
    app.include_router(routes_chat.router, prefix="/api")

    with TestClient(app) as client:
        response = client.get("/api/chat/proactive-suggestions")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "from_store"


def test_run_event_publish_allocates_contiguous_sequences_concurrently(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
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


def test_run_event_publish_redacts_payload_before_storage(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    bus = RunEventBus()
    run_id = "run_storage_redaction"

    event = bus.publish(
        run_id,
        "tool.proposed",
        {
            "index": 7,
            "structured_payload": {
                "api_key": "sk-storageeventapikey1234567890",
                "password": "storage-password-secret",
                "note": "Authorization: Bearer storagebearertoken1234567890",
            },
        },
    )

    [stored] = db.fetch_run_events(run_id, limit=10)
    stored_text = json.dumps(stored, ensure_ascii=False)
    assert event.payload["index"] == 7
    assert stored["payload"]["index"] == 7
    assert "sk-storageeventapikey1234567890" not in stored_text
    assert "storage-password-secret" not in stored_text
    assert "storagebearertoken1234567890" not in stored_text
    assert stored["payload"]["structured_payload"]["api_key"] == "***"
    assert stored["payload"]["structured_payload"]["password"] == "***"
    assert stored["payload"]["structured_payload"]["note"] == "Authorization: Bearer [REDACTED]"


def test_os_run_denies_r4_tool_without_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
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


def test_broad_drive_cleanup_uses_cleanup_plan_not_trash_path(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "old-installer.zip").write_bytes(b"0" * (2 * 1024 * 1024))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(workspace))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    db.init_db()

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "清理d盘文件", "mode": "efficiency", "engine": "os"},
        )
        assert created.status_code == 200
        final = _wait_for_phase(client, created.json()["run_id"], "completed", "failed")
        assert final["phase"] == "completed"
        plans = db.fetch_many("plans", limit=10)
        assert plans
        step = plans[0]["steps"][0]
        assert step["tool_name"] == "file.cleanup_plan"
        assert step["args"]["roots"] == [str(workspace)]


def test_cancelled_run_is_not_overwritten_by_finishing_engine_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    started = threading.Event()
    release = threading.Event()
    original_router_factory = run_service._engine_router

    class BlockingRouter:
        max_turns = 1

        async def start_run(self, goal, mode, engine, *, task_metadata=None):  # noqa: ANN001, ANN202
            return await original_router_factory(run_service.get_effective_settings()).start_run(
                goal, mode, engine, task_metadata=task_metadata
            )

        async def run_turn(self, state):  # noqa: ANN001, ANN202
            started.set()
            assert await asyncio.to_thread(release.wait, timeout=5)
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


def test_developer_cancel_terminates_fake_lengrvis_and_publishes_diagnostics(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_API_KEY", "test-api-key")
    monkeypatch.setenv("LENGRVIS_MODEL", "openai/gpt-5")
    record_path = tmp_path / "fake-lengrvis-started.json"
    fake_cli = tmp_path / "fake_lengrvis_sleep.py"
    fake_cli.write_text(
        """
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--print", action="store_true")
parser.add_argument("--output-format")
parser.add_argument("--verbose", action="store_true")
parser.add_argument("--bare", action="store_true")
parser.add_argument("--model")
parser.add_argument("--max-turns")
parser.add_argument("--add-dir")
parser.add_argument("--permission-mode")
parser.add_argument("--disallowedTools")
parser.add_argument("--allowedTools")
parser.add_argument("prompt")
args = parser.parse_args()

Path(os.environ["LENGRVIS_FAKE_RECORD"]).write_text(json.dumps({"argv": sys.argv[1:]}), encoding="utf-8")
print(json.dumps({"type": "system", "subtype": "init", "tools": args.allowedTools.split(",")}), flush=True)

def handle_signal(signum, frame):
    print(
        json.dumps(
            {"type": "result", "subtype": "error_during_execution", "is_error": True, "errors": ["terminated"]}
        ),
        flush=True,
    )
    sys.exit(23)

signal.signal(signal.SIGTERM, handle_signal)
time.sleep(30)
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_FAKE_RECORD", str(record_path))
    monkeypatch.setenv("LENGRVIS_CODE_COMMAND", f'"{sys.executable}" -u "{fake_cli}"')
    db.init_db()

    with TestClient(_test_app()) as client:
        created = client.post(
            "/api/runs",
            json={"message": "fix backend pytest slowly", "mode": "efficiency", "engine": "developer"},
        ).json()
        for _ in range(100):
            if record_path.exists():
                break
            time.sleep(0.05)
        assert record_path.exists()

        cancelled = client.post(f"/api/runs/{created['run_id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["phase"] == "cancelled"
        final = _wait_for_phase(client, created["run_id"], "cancelled")
        assert final["phase"] == "cancelled"

        for _ in range(100):
            timeline = client.get(f"/api/runs/{created['run_id']}/timeline").json()
            tool_results = [event for event in timeline["events"] if event["name"] == "tool.result"]
            if any(
                event["payload"].get("tool_name") == "lengrvis_code"
                and event["payload"].get("output", {}).get("cancelled") is True
                for event in tool_results
            ):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("cancelled Lengrvis Code tool.result was not published")


def test_paused_run_is_not_overwritten_by_finishing_engine_turn(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    started = threading.Event()
    release = threading.Event()
    original_router_factory = run_service._engine_router

    class BlockingRouter:
        max_turns = 1

        async def start_run(self, goal, mode, engine, *, task_metadata=None):  # noqa: ANN001, ANN202
            return await original_router_factory(run_service.get_effective_settings()).start_run(
                goal, mode, engine, task_metadata=task_metadata
            )

        async def run_turn(self, state):  # noqa: ANN001, ANN202
            started.set()
            assert await asyncio.to_thread(release.wait, timeout=5)
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
