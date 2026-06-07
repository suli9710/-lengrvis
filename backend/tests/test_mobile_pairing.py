from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import routes_remote
from app.llm.registry import get_effective_settings
from app.core import db
from app.core.schemas import Approval, ChatResponse, Plan, PlanStep, Task
from app.orchestration.execution_stage import ExecutionStage
from app.orchestration.task_phase import TaskPhase
from app.main import app
from app.security.mobile_jwt import MOBILE_AUTH_WS_PROTOCOL_PREFIX, REMOTE_INPUT_SCOPE, TOKEN_SCOPE, decode_mobile_token, issue_mobile_token
from app.security import mobile_jwt
from app.security.sensitive_confirmation import create_settings_confirmation
from app.api.routes_mobile import _mobile_event_allowed
from app.services import mobile_pairing_service
from app.services.approval_event_service import publish_approval_created
from app.services.settings_service import update_settings
from app.tools.registry import register_all_tools


def test_pair_request_generates_code(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)

    response = client.post("/api/pair/request")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["code"]) == 6
    int(payload["code"], 16)
    assert payload["expires_in"] <= 300
    assert payload["server"]["scheme"] == "http"
    assert payload["server"]["transport_security"]["status"] == "http_lan_insecure"
    assert payload["server"]["transport_security"]["tls_ready"] is False


def test_pair_request_reports_lan_tls_misconfiguration(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(tmp_path / "missing.crt"))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(tmp_path / "missing.key"))
    db.init_db()
    client = TestClient(app)

    payload = client.post("/api/pair/request").json()

    security = payload["server"]["transport_security"]
    assert payload["server"]["scheme"] == "https"
    assert security["status"] == "https_misconfigured"
    assert security["https_enabled"] is True
    assert security["tls_ready"] is False
    assert security["cert_present"] is False
    assert security["key_present"] is False
    assert "missing" in security["warning"].lower()


def test_pair_request_reports_lan_tls_ready(monkeypatch, tmp_path):
    cert = tmp_path / "lan.crt"
    key = tmp_path / "lan.key"
    cert.write_text("fake cert for readiness metadata", encoding="utf-8")
    key.write_text("fake key for readiness metadata", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(key))
    monkeypatch.setenv("LENGRVIS_LAN_PUBLIC_BASE_URL", "https://lengrvis.local:8443")
    db.init_db()
    client = TestClient(app)

    payload = client.post("/api/pair/request").json()

    security = payload["server"]["transport_security"]
    assert payload["server"]["scheme"] == "https"
    assert payload["server"]["origin"] == "https://lengrvis.local:8443"
    assert security["status"] == "https_ready"
    assert security["origin"] == "https://lengrvis.local:8443"
    assert security["tls_ready"] is True
    assert security["requires_trust"] is True
    assert security["trust_model"] == "local_certificate"


def test_backend_port_defaults_to_8000_with_single_env_lookup(monkeypatch):
    calls: list[str] = []

    class TrackingEnviron(dict):
        def get(self, key, default=None):
            calls.append(key)
            return super().get(key, default)

    monkeypatch.setattr(os, "environ", TrackingEnviron())

    assert mobile_pairing_service._backend_port() == 8000
    assert calls == ["LENGRVIS_BACKEND_PORT"]


def test_backend_port_uses_env_value(monkeypatch):
    monkeypatch.setenv("LENGRVIS_BACKEND_PORT", "9137")

    assert mobile_pairing_service._backend_port() == 9137


def test_pair_confirm_valid_code(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    code = client.post("/api/pair/request").json()["code"]

    response = client.post("/api/pair/confirm", json={"code": code, "device_name": "Pixel"})

    assert response.status_code == 200
    token = response.json()["token"]
    claims = decode_mobile_token(token)
    assert claims["device_id"]
    assert claims["device_name"] == "Pixel"


def test_pair_confirm_expired_code(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    code = client.post("/api/pair/request").json()["code"]
    expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.connect() as conn:
        row = conn.execute("SELECT data FROM mobile_pairings WHERE id = ?", (code,)).fetchone()
        data = json.loads(row["data"])
        data["expires_at"] = expired_at
        conn.execute(
            """
            UPDATE mobile_pairings
            SET expires_at = ?,
                data = ?
            WHERE id = ?
            """,
            (expired_at, json.dumps(data), code),
        )

    response = client.post("/api/pair/confirm", json={"code": code, "device_name": "Pixel"})

    assert response.status_code == 401


def test_mobile_endpoint_requires_jwt(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)

    response = client.get("/api/mobile/approvals/pending")
    tasks_response = client.get("/api/mobile/tasks")
    task_create_response = client.post("/api/mobile/tasks", json={"template_id": "check_computer_status"})
    task_follow_up_response = client.post("/api/mobile/tasks/task_missing/follow-up", json={"instruction": "继续"})
    grant_response = client.delete("/api/mobile/remote-input-grants/rig_missing")

    assert response.status_code == 401
    assert tasks_response.status_code == 401
    assert task_create_response.status_code == 401
    assert task_follow_up_response.status_code == 401
    assert grant_response.status_code == 401


def test_mobile_companion_lists_task_summaries_without_plan_args(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    task = Task(
        user_goal="整理下载目录并给出清理建议",
        mode="hybrid",
        final_summary="已扫描文件，等待用户确认。",
    )
    db.upsert_model("tasks", task)
    plan = Plan(
        task_id=task.id,
        goal=task.user_goal,
        steps=[
            PlanStep(
                order=1,
                agent_name="FileAgent",
                tool_name="file.cleanup",
                description="scan",
                args={"secret_path": "C:/Users/example/private.txt"},
            )
        ],
    )
    db.upsert_model("plans", plan)

    response = client.get("/api/mobile/tasks", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()["tasks"][0]
    assert payload["id"] == task.id
    assert payload["title"] == "整理下载目录并给出清理建议"
    assert payload["mode"] == "hybrid"
    assert payload["summary"] == "已扫描文件，等待用户确认。"
    assert "steps" not in payload
    assert "args" not in payload
    assert "secret_path" not in json.dumps(payload)


def test_mobile_companion_redacts_privacy_task_text(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    task = Task(
        user_goal="总结 C:/Users/example/private-contract.txt 的付款条款",
        mode="privacy",
        final_summary="private-contract.txt 写了 100 万付款。",
    )
    db.upsert_model("tasks", task)

    response = client.get("/api/mobile/tasks", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()["tasks"][0]
    assert payload["id"] == task.id
    assert payload["title"] == "隐私任务"
    assert payload["summary"] == "隐私模式：请在电脑端查看任务详情。"
    assert "private-contract" not in json.dumps(payload, ensure_ascii=False)
    assert "100 万" not in json.dumps(payload, ensure_ascii=False)


def test_mobile_companion_can_cancel_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    task = Task(user_goal="稍后取消的任务")
    db.upsert_model("tasks", task)

    response = client.post(
        f"/api/mobile/tasks/{task.id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == task.id
    assert response.json()["status"] == "cancelled"


def test_mobile_companion_cannot_cancel_terminal_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    task = Task(user_goal="已经完成的任务", status=TaskPhase.COMPLETED)
    db.upsert_model("tasks", task)

    response = client.post(
        f"/api/mobile/tasks/{task.id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    refreshed = Task.model_validate(db.fetch_one("tasks", task.id))
    assert refreshed.status == TaskPhase.COMPLETED


def test_mobile_companion_can_pause_and_resume_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    task = Task(
        user_goal="可暂停的任务",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.STEP_RUNNING,
    )
    db.upsert_model("tasks", task)

    response = client.post(
        f"/api/mobile/tasks/{task.id}/pause",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "paused"

    def fake_resume_task(task_id: str, *, strict: bool | None = None) -> Task:
        assert strict is True
        resumed = Task.model_validate(db.fetch_one("tasks", task_id))
        resumed.status = TaskPhase.EXECUTION
        resumed.phase = TaskPhase.EXECUTION
        resumed.execution_stage = ExecutionStage.STEP_RUNNING
        db.upsert_model("tasks", resumed)
        return resumed

    monkeypatch.setattr("app.api.routes_mobile.resume_task", fake_resume_task)
    response = client.post(
        f"/api/mobile/tasks/{task.id}/resume",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "execution"


def test_mobile_companion_cannot_pause_waiting_approval_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    task = Task(
        user_goal="等待审批的任务",
        status=TaskPhase.EXECUTION,
        execution_stage=ExecutionStage.AWAITING_APPROVAL,
    )
    db.upsert_model("tasks", task)

    response = client.post(
        f"/api/mobile/tasks/{task.id}/pause",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Only actively running tasks can be paused."
    refreshed = Task.model_validate(db.fetch_one("tasks", task.id))
    assert refreshed.execution_stage == ExecutionStage.AWAITING_APPROVAL


def test_mobile_companion_can_start_template_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    captured: dict[str, str] = {}

    def fake_delegate_mobile_task(goal: str, mode: str, *, reply: str, agent_hint: str) -> ChatResponse:
        captured.update({"goal": goal, "mode": mode, "agent_hint": agent_hint})
        task = Task(user_goal=goal, mode=mode)
        db.upsert_model("tasks", task)
        return ChatResponse(task_id=task.id, status=task.status, message=reply, delegated=True, agent=agent_hint)

    monkeypatch.setattr("app.api.routes_mobile._delegate_mobile_task", fake_delegate_mobile_task)

    response = client.post(
        "/api/mobile/tasks",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "template_id": "organize_downloads",
            "user_input": "只扫描 D:/Downloads，先不要删除。",
            "mode": "hybrid",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["task"]["id"]
    assert payload["task"]["mode"] == "hybrid"
    assert payload["message"].startswith("已从手机 Companion 发起")
    assert "整理下载目录" in captured["goal"]
    assert "D:/Downloads" in captured["goal"]
    assert "dry-run" in captured["goal"]
    assert captured["agent_hint"] == "FileAgent"


def test_mobile_companion_task_start_conflicts_when_no_computer_task_created(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)

    def fake_delegate_mobile_task(goal: str, mode: str, *, reply: str, agent_hint: str) -> ChatResponse:
        return ChatResponse(message="需要在电脑端补充任务范围。", delegated=False, agent=agent_hint)

    monkeypatch.setattr("app.api.routes_mobile._delegate_mobile_task", fake_delegate_mobile_task)

    response = client.post(
        "/api/mobile/tasks",
        headers={"Authorization": f"Bearer {token}"},
        json={"template_id": "document_qa", "mode": "hybrid"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "需要在电脑端补充任务范围。"


def test_mobile_companion_task_start_returns_stable_error_when_delegate_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)

    def fake_delegate_mobile_task(goal: str, mode: str, *, reply: str, agent_hint: str) -> ChatResponse:
        raise RuntimeError("task bus unavailable")

    monkeypatch.setattr("app.api.routes_mobile._delegate_mobile_task", fake_delegate_mobile_task)

    response = client.post(
        "/api/mobile/tasks",
        headers={"Authorization": f"Bearer {token}"},
        json={"template_id": "check_computer_status", "mode": "efficiency"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Computer task service is unavailable. Please retry from the desktop task workspace."


def test_mobile_companion_follow_up_creates_related_computer_task(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    source = Task(user_goal="整理下载目录", mode="hybrid")
    db.upsert_model("tasks", source)
    captured: dict[str, str] = {}

    def fake_delegate_mobile_task(goal: str, mode: str, *, reply: str, agent_hint: str) -> ChatResponse:
        captured.update({"goal": goal, "mode": mode, "agent_hint": agent_hint})
        task = Task(user_goal=goal, mode=mode)
        db.upsert_model("tasks", task)
        return ChatResponse(task_id=task.id, status=task.status, message=reply, delegated=True, agent=agent_hint)

    monkeypatch.setattr("app.api.routes_mobile._delegate_mobile_task", fake_delegate_mobile_task)

    response = client.post(
        f"/api/mobile/tasks/{source.id}/follow-up",
        headers={"Authorization": f"Bearer {token}"},
        json={"instruction": "再检查一下桌面上的临时文件。"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["source_task_id"] == source.id
    assert payload["task"]["id"] != source.id
    assert payload["task"]["mode"] == "hybrid"
    assert "补充指令" in captured["goal"]
    assert "桌面上的临时文件" in captured["goal"]


def test_mobile_companion_follow_up_does_not_echo_privacy_task_text(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    source = Task(
        user_goal="总结 C:/Users/example/private-contract.txt 的付款条款",
        mode="privacy",
        final_summary="private-contract.txt 写了 100 万付款。",
    )
    db.upsert_model("tasks", source)
    captured: dict[str, str] = {}

    def fake_delegate_mobile_task(goal: str, mode: str, *, reply: str, agent_hint: str) -> ChatResponse:
        captured.update({"goal": goal, "mode": mode})
        task = Task(user_goal=goal, mode=mode, final_summary="private-contract.txt still secret")
        db.upsert_model("tasks", task)
        return ChatResponse(task_id=task.id, status=task.status, message=reply, delegated=True, agent=agent_hint)

    monkeypatch.setattr("app.api.routes_mobile._delegate_mobile_task", fake_delegate_mobile_task)

    response = client.post(
        f"/api/mobile/tasks/{source.id}/follow-up",
        headers={"Authorization": f"Bearer {token}"},
        json={"instruction": "继续用隐私模式检查第二部分。"},
    )

    assert response.status_code == 201
    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert response.json()["task"]["title"] == "隐私任务"
    assert "private-contract" not in payload_text
    assert "100 万" not in payload_text
    assert "private-contract" not in captured["goal"]


def test_pair_code_can_be_redeemed_once_for_mobile_jwt(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)

    code_response = client.post("/api/pair/code")
    assert code_response.status_code == 200
    code = code_response.json()["code"]
    assert len(code) == 6

    pair_response = client.post("/api/pair", json={"code": code, "device_name": "Pixel"})
    assert pair_response.status_code == 200
    token = pair_response.json()["token"]
    claims = decode_mobile_token(token)
    assert claims["device_name"] == "Pixel"
    assert claims["scope"] == "mobile:approval"

    replay_response = client.post("/api/pair", json={"code": code, "device_name": "Replay"})
    assert replay_response.status_code == 401


def test_pair_code_includes_remote_view_scope_only_when_remote_desktop_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    patch = {"remote_desktop_enabled": True}
    confirmation = create_settings_confirmation(patch)
    if confirmation.get("required"):
        patch["confirmation_nonce"] = confirmation["nonce"]
    update_settings(patch)
    client = TestClient(app)

    token = _paired_token(client)
    claims = decode_mobile_token(token)

    assert set(claims["scope"].split()) == {"mobile:approval", "remote:view"}


def test_mobile_token_survives_backend_process_restart(tmp_path):
    data_dir = tmp_path / "data"
    token = _run_mobile_jwt_subprocess(
        (
            "from app.security.mobile_jwt import issue_mobile_token; "
            "print(issue_mobile_token(device_id='mobile_restart', device_name='Restart Phone'))"
        ),
        data_dir,
    )

    claims_json = _run_mobile_jwt_subprocess(
            (
                "import json, os; "
                "from app.security.mobile_jwt import decode_mobile_token; "
                "print(json.dumps(decode_mobile_token(os.environ['LENGRVIS_TEST_TOKEN'], require_active_device=False), sort_keys=True))"
            ),
        data_dir,
        {"LENGRVIS_TEST_TOKEN": token},
    )
    claims = json.loads(claims_json)

    assert claims["device_id"] == "mobile_restart"
    assert claims["device_name"] == "Restart Phone"
    assert (data_dir / "mobile_jwt.secret").read_text(encoding="utf-8").strip()


def test_pair_code_redeem_is_atomic_under_concurrent_submitters(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _clear_pairing_failures()
    code = mobile_pairing_service.create_pairing_request()["code"]
    barrier = threading.Barrier(2)

    def redeem(index: int) -> tuple[str, str | int]:
        barrier.wait(timeout=5)
        try:
            payload = mobile_pairing_service.confirm_pairing(
                code=code,
                device_name=f"Phone {index}",
                client_host=f"198.51.100.{index}",
            )
            return ("ok", payload["device_id"])
        except HTTPException as exc:
            return ("error", exc.status_code)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(redeem, [1, 2]))

    assert [kind for kind, _value in results].count("ok") == 1
    assert [value for kind, value in results if kind == "error"] == [401]
    record = db.fetch_one("mobile_pairings", code)
    assert record is not None
    assert record["status"] == "used"
    assert len(db.fetch_many("mobile_devices", limit=10)) == 1


def test_pair_confirm_rate_limits_failed_attempts_by_client(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _clear_pairing_failures()
    client = TestClient(app, client=("192.0.2.88", 50100))

    for _ in range(mobile_pairing_service.PAIR_CONFIRM_FAILURE_LIMIT):
        response = client.post("/api/pair/confirm", json={"code": "ffffff", "device_name": "Phone"})
        assert response.status_code == 401

    limited = client.post("/api/pair/confirm", json={"code": "ffffff", "device_name": "Phone"})

    assert limited.status_code == 429


def test_mobile_approval_routes_require_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)

    response = client.get("/api/mobile/approvals/pending")

    assert response.status_code == 401


def test_revoked_mobile_device_token_cannot_use_mobile_api_or_remote_screen(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    device_id = decode_mobile_token(token)["device_id"]
    approval = Approval(task_id="task_revoked_mobile", step_id="step_1", message="Approve revoked test")
    db.upsert_model("approvals", approval)

    revoke_response = client.delete(f"/api/pair/devices/{device_id}")
    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    devices_response = client.get("/api/pair/devices")

    assert revoke_response.status_code == 200
    assert revoke_response.json()["status"] == "revoked"
    assert pending_response.status_code == 401
    assert devices_response.status_code == 200
    assert all(device["device_id"] != device_id for device in devices_response.json()["devices"])


def test_mobile_device_list_only_returns_calling_device(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    first_token = _paired_token(client)
    second_token = _paired_token(client)
    first_device_id = decode_mobile_token(first_token)["device_id"]
    second_device_id = decode_mobile_token(second_token)["device_id"]

    response = client.get("/api/mobile/devices", headers={"Authorization": f"Bearer {first_token}"})

    assert response.status_code == 200
    assert [device["device_id"] for device in response.json()["devices"]] == [first_device_id]
    assert second_device_id not in [device["device_id"] for device in response.json()["devices"]]


def test_mobile_device_can_only_revoke_itself(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    first_token = _paired_token(client)
    second_token = _paired_token(client)
    first_device_id = decode_mobile_token(first_token)["device_id"]
    second_device_id = decode_mobile_token(second_token)["device_id"]

    cross_response = client.delete(
        f"/api/mobile/devices/{second_device_id}",
        headers={"Authorization": f"Bearer {first_token}"},
    )
    own_response = client.delete(
        f"/api/mobile/devices/{first_device_id}",
        headers={"Authorization": f"Bearer {first_token}"},
    )

    assert cross_response.status_code == 403
    assert db.fetch_one("mobile_devices", second_device_id)["status"] == "active"
    assert own_response.status_code == 200
    assert db.fetch_one("mobile_devices", first_device_id)["status"] == "revoked"


def test_mobile_can_list_and_decide_pending_approvals(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    approval = Approval(task_id="task_mobile", step_id="step_1", message="Approve mobile test")
    db.upsert_model("approvals", approval)

    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert pending_response.status_code == 200
    assert pending_response.json()[0]["id"] == approval.id

    decision_response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )
    assert decision_response.status_code == 200
    assert decision_response.json()["status"] == "rejected"


def test_mobile_filters_and_blocks_cross_device_approvals(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    first_token = _paired_token(client)
    second_token = _paired_token(client)
    first_device_id = decode_mobile_token(first_token)["device_id"]
    second_device_id = decode_mobile_token(second_token)["device_id"]
    first_approval = Approval(
        task_id="task_mobile_first_device",
        step_id="step_1",
        message="Approve first device test",
        allowed_device_ids=[first_device_id],
    )
    second_approval = Approval(
        task_id="task_mobile_second_device",
        step_id="step_1",
        message="Approve second device test",
        allowed_device_ids=[second_device_id],
    )
    db.upsert_model("approvals", first_approval)
    db.upsert_model("approvals", second_approval)

    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {first_token}"},
    )
    cross_device_response = client.post(
        f"/api/mobile/approvals/{second_approval.id}/decision",
        headers={"Authorization": f"Bearer {first_token}"},
        json={"decision": "denied"},
    )
    allowed_response = client.post(
        f"/api/mobile/approvals/{first_approval.id}/decision",
        headers={"Authorization": f"Bearer {first_token}"},
        json={"decision": "denied"},
    )

    assert pending_response.status_code == 200
    assert [approval["id"] for approval in pending_response.json()] == [first_approval.id]
    assert cross_device_response.status_code == 403
    assert db.fetch_one("approvals", second_approval.id)["status"] == "pending"
    assert allowed_response.status_code == 200
    assert allowed_response.json()["status"] == "rejected"


def test_mobile_approval_scope_cannot_decide_remote_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    approval = Approval(
        task_id="task_mobile_remote_input",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input test",
    )
    db.upsert_model("approvals", approval)

    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    decision_response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert pending_response.status_code == 200
    assert pending_response.json() == []
    assert decision_response.status_code == 403
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_same_device_mobile_approval_scope_cannot_decide_remote_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    device_id = decode_mobile_token(token)["device_id"]
    approval = Approval(
        task_id="task_mobile_remote_input_same_device",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve same-device remote input test",
        source_device_id=device_id,
        allowed_device_ids=[device_id],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    decision_response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert pending_response.status_code == 200
    assert pending_response.json() == []
    assert decision_response.status_code == 403
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_remote_input_scope_can_decide_remote_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)
    approval = Approval(
        task_id="task_mobile_remote_input_allowed",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input with scope",
        source_device_id=device_id,
        source_grant_id=grant["grant_id"],
        allowed_device_ids=[device_id],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_remote_input_grant_cannot_decide_approval_from_other_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    source_grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    other_grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    other_token = _claim_remote_input_token(client, paired_token, other_grant)
    approval = Approval(
        task_id="task_mobile_remote_input_other_grant",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input from source grant",
        source_device_id=device_id,
        source_grant_id=source_grant["grant_id"],
        allowed_device_ids=[device_id],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {other_token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 403
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_remote_input_grant_cannot_decide_approval_missing_grant_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)
    approval = Approval(
        task_id="task_mobile_remote_input_missing_grant",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input without grant binding",
        source_device_id=device_id,
        allowed_device_ids=[device_id],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 403
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_remote_input_scope_cannot_decide_after_remote_desktop_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)
    approval = Approval(
        task_id="task_mobile_remote_input_disabled",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input after remote desktop disabled",
        source_device_id=device_id,
        allowed_device_ids=[device_id],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)
    update_settings({"remote_desktop_enabled": False})

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Remote desktop is disabled"
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_remote_input_scope_cannot_decide_unbound_remote_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)
    approval = Approval(
        task_id="task_mobile_remote_input_unbound",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve unbound remote input",
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 403
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_remote_input_scope_cannot_decide_other_device_remote_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    first_token = _paired_token(client)
    second_token = _paired_token(client)
    first_device_id = decode_mobile_token(first_token)["device_id"]
    second_device_id = decode_mobile_token(second_token)["device_id"]
    first_grant = client.post(f"/api/pair/devices/{first_device_id}/remote-input-grants").json()
    first_remote_token = _claim_remote_input_token(client, first_token, first_grant)
    approval = Approval(
        task_id="task_mobile_remote_input_other_device",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve other device remote input",
        source_device_id=second_device_id,
        allowed_device_ids=[second_device_id],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {first_remote_token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 403
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_remote_input_scope_requires_grant_source_for_approval_claims(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    token = issue_mobile_token(device_id=device_id, device_name="Test Phone", scope=REMOTE_INPUT_SCOPE)
    approval = Approval(
        task_id="task_mobile_remote_input_plain_scope",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve plain remote input scope",
        source_device_id=device_id,
        allowed_device_ids=[device_id],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 401
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_remote_input_approval_policy_requires_grant_backed_claims(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_policy", device_name="Policy Phone")
    approval = Approval(
        task_id="task_mobile_remote_input_policy",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input through policy",
        source_device_id="mobile_policy",
        allowed_device_ids=["mobile_policy"],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )

    allowed = mobile_pairing_service.mobile_claims_can_access_approval(
        approval,
        {
            "device_id": "mobile_policy",
            "device_name": "Policy Phone",
            "scope": REMOTE_INPUT_SCOPE,
        },
    )

    assert allowed is False


def test_desktop_can_issue_short_lived_remote_input_grant_for_paired_device(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]

    grant_response = client.post(f"/api/pair/devices/{device_id}/remote-input-grants")

    assert grant_response.status_code == 200
    payload = grant_response.json()
    assert "token" not in payload
    assert "token_type" not in payload
    assert payload["expires_in"] <= mobile_pairing_service.REMOTE_INPUT_GRANT_TTL_SECONDS
    assert all(grant["status"] == "active" for grant in payload["device"]["remote_input_grants"])
    assert all("token" not in grant for grant in payload["device"]["remote_input_grants"])


def test_remote_input_grant_creation_requires_remote_desktop_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]

    response = client.post(f"/api/pair/devices/{device_id}/remote-input-grants")

    assert response.status_code == 403
    assert db.fetch_one("mobile_devices", device_id)["remote_input_grants"] == []


def test_remote_input_grant_claim_requires_remote_desktop_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    update_settings({"remote_desktop_enabled": False})

    response = client.post(
        f"/api/mobile/remote-input-grants/{grant['grant_id']}/token",
        headers={"Authorization": f"Bearer {paired_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Remote desktop is disabled"
    assert "token" not in response.json()
    device = db.fetch_one("mobile_devices", device_id)
    assert device["remote_input_grants"][0]["status"] == "active"


def test_revoking_remote_input_grant_invalidates_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)

    revoke_response = client.delete(f"/api/pair/devices/{device_id}/remote-input-grants/{grant['grant_id']}")

    assert revoke_response.status_code == 200
    assert revoke_response.json()["status"] == "revoked"
    assert "token" not in json.dumps(client.get("/api/pair/devices").json(), ensure_ascii=False)
    response = client.post(
        f"/api/mobile/approvals/{Approval(task_id='unused', step_id='step_1', approval_type='remote_input', message='unused').id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )
    assert response.status_code == 401


def test_revoking_mobile_device_invalidates_remote_input_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)
    approval = Approval(
        task_id="task_revoked_remote_input_grant",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve revoked remote input grant",
    )
    db.upsert_model("approvals", approval)

    revoke_response = client.delete(f"/api/pair/devices/{device_id}")
    decision_response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert revoke_response.status_code == 200
    assert decision_response.status_code == 401
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_mobile_device_revoke_is_atomic_with_remote_input_grant_creation(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_atomic_revoke", device_name="Atomic")
    barrier = threading.Barrier(2)
    results: list[tuple[str, str]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def create_grant() -> None:
        try:
            barrier.wait(timeout=5)
            grant = mobile_pairing_service.create_remote_input_grant("mobile_atomic_revoke")
            with lock:
                results.append(("grant", grant["grant_id"]))
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    def revoke_device() -> None:
        try:
            barrier.wait(timeout=5)
            revoked = mobile_pairing_service.revoke_mobile_device("mobile_atomic_revoke")
            with lock:
                results.append(("revoke", revoked["status"]))
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    grant_thread = threading.Thread(target=create_grant)
    revoke_thread = threading.Thread(target=revoke_device)
    grant_thread.start()
    revoke_thread.start()
    grant_thread.join(timeout=5)
    revoke_thread.join(timeout=5)

    assert not grant_thread.is_alive()
    assert not revoke_thread.is_alive()
    assert [kind for kind, _value in results].count("revoke") == 1
    assert all(getattr(error, "status_code", None) == 409 for error in errors)
    device = db.fetch_one("mobile_devices", "mobile_atomic_revoke")
    assert device is not None
    assert device["status"] == "revoked"
    grants_by_id = {grant["id"]: grant for grant in device["remote_input_grants"]}
    for kind, value in results:
        if kind == "grant":
            assert grants_by_id[value]["status"] == "revoked"
    assert all(grant["status"] == "revoked" for grant in device["remote_input_grants"])


def test_expired_remote_input_grant_token_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)
    device = db.fetch_one("mobile_devices", device_id)
    assert device is not None
    device["remote_input_grants"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), device_id),
        )
    approval = Approval(
        task_id="task_expired_remote_input_grant",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve expired remote input grant",
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 401
    assert db.fetch_one("approvals", approval.id)["status"] == "pending"


def test_remote_input_grant_can_decide_remote_input(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)
    approval = Approval(
        task_id="task_remote_input_grant_allowed",
        step_id="step_1",
        approval_type="remote_input",
        message="Approve remote input grant",
        source_device_id=device_id,
        source_grant_id=grant["grant_id"],
        allowed_device_ids=[device_id],
        required_mobile_scopes=[REMOTE_INPUT_SCOPE],
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "denied"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_mobile_websocket_receives_remote_input_grant_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{paired_token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"

        grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
        event = websocket.receive_json()

    event_text = json.dumps(event, ensure_ascii=False)
    assert event["type"] == "remote_input_grant_created"
    assert event["device_id"] == device_id
    assert event["grant"]["id"] == grant["grant_id"]
    assert "token" not in grant
    assert "token" not in event["grant"]


def test_mobile_websocket_receives_remote_input_grant_revoked_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{paired_token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"

        client.delete(f"/api/pair/devices/{device_id}/remote-input-grants/{grant['grant_id']}")
        event = websocket.receive_json()

    event_text = json.dumps(event, ensure_ascii=False)
    assert event["type"] == "remote_input_grant_revoked"
    assert event["device_id"] == device_id
    assert event["grant"]["id"] == grant["grant_id"]
    assert event["grant"]["status"] == "revoked"
    assert "token" not in grant
    assert "token" not in event["grant"]


def test_remote_input_grant_full_websocket_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{paired_token}"],
    ) as mobile_websocket:
        assert mobile_websocket.receive_json()["type"] == "connected"

        grant_response = client.post(f"/api/pair/devices/{device_id}/remote-input-grants")
        assert grant_response.status_code == 200
        grant = grant_response.json()
        created_event = mobile_websocket.receive_json()
        assert created_event["type"] == "remote_input_grant_created"
        assert created_event["device_id"] == device_id
        assert created_event["grant"]["id"] == grant["grant_id"]
        assert "token" not in grant
        assert "token" not in json.dumps(created_event, ensure_ascii=False)

        claim_response = client.post(
            f"/api/mobile/remote-input-grants/{grant['grant_id']}/token",
            headers={"Authorization": f"Bearer {paired_token}"},
        )
        assert claim_response.status_code == 200
        claimed = claim_response.json()

        with client.websocket_connect(
            "/ws/remote/input",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{claimed['token']}"],
        ) as input_websocket:
            assert input_websocket.receive_json()["type"] == "connected"

            input_websocket.send_json({"type": "click", "x": 101, "y": 202})
            input_result = input_websocket.receive_json()
            assert input_result["type"] == "approval_required"
            approval_id = input_result["approval_id"]
            approval = db.fetch_one("approvals", approval_id)
            assert approval is not None
            assert approval["approval_type"] == "remote_input"
            assert approval["source_device_id"] == device_id
            assert approval["source_grant_id"] == grant["grant_id"]
            assert approval["allowed_device_ids"] == [device_id]
            assert approval["required_mobile_scopes"] == [REMOTE_INPUT_SCOPE]

            revoke_response = client.delete(f"/api/pair/devices/{device_id}/remote-input-grants/{grant['grant_id']}")
            assert revoke_response.status_code == 200
            revoked_event = mobile_websocket.receive_json()
            assert revoked_event["type"] == "remote_input_grant_revoked"
            assert revoked_event["device_id"] == device_id
            assert revoked_event["grant"]["id"] == grant["grant_id"]
            assert revoked_event["grant"]["status"] == "revoked"
            assert "token" not in json.dumps(revoked_event, ensure_ascii=False)

            input_websocket.send_json({"type": "click", "x": 1, "y": 2})
            with pytest.raises(WebSocketDisconnect) as exc_info:
                input_websocket.receive_json()

    assert exc_info.value.code == 1008


def test_mobile_websocket_filters_other_device_remote_input_grant_revoked(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    first_token = _paired_token(client)
    second_token = _paired_token(client)
    first_device_id = decode_mobile_token(first_token)["device_id"]
    second_device_id = decode_mobile_token(second_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{second_device_id}/remote-input-grants").json()
    revoked = client.delete(f"/api/pair/devices/{second_device_id}/remote-input-grants/{grant['grant_id']}").json()

    allowed = _mobile_event_allowed(
        {"type": "remote_input_grant_revoked", "device_id": second_device_id, "grant": revoked},
        {"device_id": first_device_id},
    )

    assert allowed is False


def test_mobile_device_can_claim_remote_input_grant_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()

    response = client.post(
        f"/api/mobile/remote-input-grants/{grant['grant_id']}/token",
        headers={"Authorization": f"Bearer {paired_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    claims = decode_mobile_token(payload["token"], allowed_scopes={REMOTE_INPUT_SCOPE})
    assert claims["device_id"] == device_id
    assert claims["grant_id"] == grant["grant_id"]
    assert payload["expires_in"] <= mobile_pairing_service.REMOTE_INPUT_GRANT_TTL_SECONDS


def test_mobile_device_can_revoke_own_remote_input_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()

    response = client.delete(
        f"/api/mobile/remote-input-grants/{grant['grant_id']}",
        headers={"Authorization": f"Bearer {paired_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == grant["grant_id"]
    assert payload["status"] == "revoked"
    device = db.fetch_one("mobile_devices", device_id)
    assert device["remote_input_grants"][0]["status"] == "revoked"


def test_mobile_device_cannot_claim_other_device_remote_input_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    first_token = _paired_token(client)
    second_token = _paired_token(client)
    second_device_id = decode_mobile_token(second_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{second_device_id}/remote-input-grants").json()

    response = client.post(
        f"/api/mobile/remote-input-grants/{grant['grant_id']}/token",
        headers={"Authorization": f"Bearer {first_token}"},
    )

    assert response.status_code == 403


def test_mobile_device_cannot_claim_revoked_remote_input_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    client.delete(f"/api/pair/devices/{device_id}/remote-input-grants/{grant['grant_id']}")

    response = client.post(
        f"/api/mobile/remote-input-grants/{grant['grant_id']}/token",
        headers={"Authorization": f"Bearer {paired_token}"},
    )

    assert response.status_code == 401


def test_mobile_device_cannot_claim_expired_remote_input_grant(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    device = db.fetch_one("mobile_devices", device_id)
    assert device is not None
    device["remote_input_grants"][0]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with db.connect() as conn:
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), device_id),
        )

    response = client.post(
        f"/api/mobile/remote-input-grants/{grant['grant_id']}/token",
        headers={"Authorization": f"Bearer {paired_token}"},
    )

    assert response.status_code == 401


def test_remote_input_scope_cannot_use_general_mobile_resources(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = _claim_remote_input_token(client, paired_token, grant)

    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    devices_response = client.get(
        "/api/mobile/devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    revoke_response = client.delete(
        f"/api/mobile/devices/{device_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert pending_response.status_code == 403
    assert devices_response.status_code == 403
    assert revoke_response.status_code == 403
    assert db.fetch_one("mobile_devices", device_id)["status"] == "active"


def test_remote_input_grant_token_cannot_use_general_mobile_resources_even_with_mobile_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    client = TestClient(app)
    paired_token = _paired_token(client)
    device_id = decode_mobile_token(paired_token)["device_id"]
    grant = client.post(f"/api/pair/devices/{device_id}/remote-input-grants").json()
    token = issue_mobile_token(
        device_id=device_id,
        device_name="Test Phone",
        scope=[TOKEN_SCOPE, REMOTE_INPUT_SCOPE],
        source="remote_input_grant",
        grant_id=grant["grant_id"],
    )

    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    devices_response = client.get(
        "/api/mobile/devices",
        headers={"Authorization": f"Bearer {token}"},
    )
    claim_response = client.post(
        f"/api/mobile/remote-input-grants/{grant['grant_id']}/token",
        headers={"Authorization": f"Bearer {token}"},
    )
    revoke_response = client.delete(
        f"/api/mobile/devices/{device_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert pending_response.status_code == 403
    assert devices_response.status_code == 403
    assert claim_response.status_code == 403
    assert revoke_response.status_code == 403
    assert db.fetch_one("mobile_devices", device_id)["status"] == "active"


def test_mobile_approval_payload_redacts_sensitive_preview(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    approval = Approval(
        task_id="task_mobile_secret",
        step_id="step_1",
        message="Approve mobile secret test",
        diff_preview={
            "ok": True,
            "diff_preview": [
                {
                    "action": "fill",
                    "field_name": "#notes",
                    "value": "token abcdef1234567890",
                    "url": "https://example.com/form?token=secret-query-token",
                }
            ],
        },
    )
    db.upsert_model("approvals", approval)
    publish_approval_created(approval)

    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    detail_response = client.get(
        f"/api/mobile/approvals/{approval.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert pending_response.status_code == 200
    assert detail_response.status_code == 200
    payload_text = json.dumps(
        {"pending": pending_response.json(), "detail": detail_response.json()},
        ensure_ascii=False,
    )
    assert "abcdef1234567890" not in payload_text
    assert "secret-query-token" not in payload_text
    assert "[REDACTED" in payload_text or "***" in payload_text


def test_mobile_approval_payload_redacts_sensitive_message(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    approval = Approval(
        task_id="task_mobile_message_secret",
        step_id="step_1",
        message="Approve operation with token=secret-token-raw-message-1234567890",
    )
    db.upsert_model("approvals", approval)
    publish_approval_created(approval)

    pending_response = client.get(
        "/api/mobile/approvals/pending",
        headers={"Authorization": f"Bearer {token}"},
    )
    detail_response = client.get(
        f"/api/mobile/approvals/{approval.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert pending_response.status_code == 200
    assert detail_response.status_code == 200
    payload_text = json.dumps({"pending": pending_response.json(), "detail": detail_response.json()}, ensure_ascii=False)
    assert "secret-token-raw-message-1234567890" not in payload_text
    assert "token=[REDACTED]" in payload_text


def test_desktop_approval_payload_hides_binding_resource_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    approval = Approval(
        task_id="task_desktop_binding",
        step_id="step_1",
        message="Approve desktop resource state test",
        diff_preview={
            "ok": True,
            "diff_preview": [{"action": "write", "path": "a.txt"}],
            "_resource_state": [
                {
                    "path": "a.txt",
                    "sha256": "internal-sha",
                    "mtime_ns": 123,
                    "inode": 456,
                    "size": 7,
                }
            ],
        },
    )
    db.upsert_model("approvals", approval)

    response = client.get("/api/approvals/pending")

    assert response.status_code == 200
    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert "_resource_state" not in payload_text
    assert "internal-sha" not in payload_text
    assert response.json()[0]["diff_preview"]["diff_preview"][0]["path"] == "a.txt"


def test_mobile_decision_response_hides_binding_resource_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    approval = Approval(
        task_id="task_mobile_binding",
        step_id="step_1",
        message="Approve mobile binding test",
        diff_preview={
            "ok": True,
            "diff_preview": [{"action": "write", "path": "a.txt"}],
            "_resource_state": [{"path": "a.txt", "sha256": "internal-sha"}],
        },
    )
    db.upsert_model("approvals", approval)

    response = client.post(
        f"/api/mobile/approvals/{approval.id}/decision",
        headers={"Authorization": f"Bearer {token}"},
        json={"decision": "rejected"},
    )

    assert response.status_code == 200
    payload_text = json.dumps(response.json(), ensure_ascii=False)
    assert "_resource_state" not in payload_text
    assert "internal-sha" not in payload_text
    assert response.json()["diff_preview"]["diff_preview"][0]["path"] == "a.txt"


def test_approval_decision_is_atomic_under_concurrent_submitters(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    approval = Approval(task_id="task_atomic", step_id="step_1", message="Approve atomically")
    db.upsert_model("approvals", approval)

    results: list[tuple[str, str]] = []
    barrier = threading.Barrier(2)

    def decide(status: str) -> None:
        barrier.wait(timeout=5)
        row = db.decide_approval_atomically(approval.id, status, datetime.now(timezone.utc).isoformat())
        results.append((status, "won" if row else "lost"))

    approve = threading.Thread(target=decide, args=("approved",))
    reject = threading.Thread(target=decide, args=("rejected",))
    approve.start()
    reject.start()
    approve.join(timeout=5)
    reject.join(timeout=5)

    assert sorted(result for _status, result in results) == ["lost", "won"]
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] in {"approved", "rejected"}
    assert stored["decided_at"]


def test_approved_approval_execution_claim_is_atomic_under_concurrent_callers(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    approval = Approval(
        task_id="task_claim_atomic",
        step_id="step_1",
        message="Claim atomically",
        status="approved",
    )
    db.upsert_model("approvals", approval)

    results: list[dict | None] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)
    lock = threading.Lock()

    def claim() -> None:
        try:
            barrier.wait(timeout=5)
            row = db.claim_approval_for_execution(approval.id, datetime.now(timezone.utc).isoformat())
            with lock:
                results.append(row)
        except BaseException as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    first = threading.Thread(target=claim)
    second = threading.Thread(target=claim)
    first.start()
    second.start()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert sum(row is not None for row in results) == 1
    stored = db.fetch_one("approvals", approval.id)
    assert stored is not None
    assert stored["status"] == "approved"
    assert stored["consumed_at"]


def test_mobile_detail_redacts_task_and_omits_plan_args(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    task = Task(user_goal="Use token super-secret-token-1234567890", final_summary="password=abc1234567890")
    step = PlanStep(
        task_id=task.id,
        order=1,
        agent_name="FileAgent",
        tool_name="file.write",
        description="Write Authorization Bearer secret-token-1234567890",
        args={"value": "token should not leak", "path": "notes.txt"},
        expected_observation="password should not leak",
    )
    plan = Plan(task_id=task.id, goal="Use token abcdef1234567890", steps=[step])
    approval = Approval(task_id=task.id, step_id=step.id, message="Approve safe detail")
    db.upsert_model("tasks", task)
    db.upsert_model("plans", plan)
    db.upsert_model("approvals", approval)

    response = client.get(
        f"/api/mobile/approvals/{approval.id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert "super-secret-token-1234567890" not in payload_text
    assert "secret-token-1234567890" not in payload_text
    assert "abc1234567890" not in payload_text
    assert "args" not in payload["plan"]["steps"][0]


def test_mobile_approval_websocket_receives_created_event(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        connected = websocket.receive_json()
        assert connected["type"] == "connected"

        approval = Approval(task_id="task_ws_mobile", step_id="step_1", message="Approve from phone")
        db.upsert_model("approvals", approval)
        publish_approval_created(approval)

        event = websocket.receive_json()

    assert event["type"] == "approval_created"
    assert event["approval"]["id"] == approval.id


def test_mobile_approval_websocket_accepts_token_subprotocol(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        connected = websocket.receive_json()

    assert connected["type"] == "connected"


def test_mobile_approval_websocket_closes_when_device_revoked(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)
    device_id = decode_mobile_token(token)["device_id"]

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        connected = websocket.receive_json()
        assert connected["type"] == "connected"

        revoked = client.delete(f"/api/pair/devices/{device_id}").json()
        event = websocket.receive_json()
        assert event["type"] == "mobile_device_revoked"
        assert event["device"]["device_id"] == device_id
        assert event["device"]["status"] == "revoked"
        assert "token" not in json.dumps(event, ensure_ascii=False)

        try:
            websocket.receive_json()
            raise AssertionError("Mobile approval WebSocket should close after its device is revoked")
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "code", None) == 1008
        assert revoked["status"] == "revoked"


def test_mobile_approval_websocket_closes_after_token_expires(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_expiring_ws", device_name="Expiring Phone")
    token = issue_mobile_token(device_id="mobile_expiring_ws", device_name="Expiring Phone", expires_in_seconds=60)
    client = TestClient(app)

    class ExpiredTokenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=120)

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        monkeypatch.setattr(mobile_jwt, "datetime", ExpiredTokenClock)
        approval = Approval(task_id="task_ws_mobile_expired", step_id="step_1", message="Expired token event")
        db.upsert_model("approvals", approval)
        publish_approval_created(approval)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_mobile_approval_websocket_rejects_remote_input_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_remote_ws", device_name="Remote Phone")
    token = issue_mobile_token(device_id="mobile_remote_ws", device_name="Remote Phone", scope=REMOTE_INPUT_SCOPE)
    client = TestClient(app)

    try:
        with client.websocket_connect(
            "/ws/mobile/approvals",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ):
            raise AssertionError("Mobile approval WebSocket should reject remote-input-only tokens")
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", None) == 1008


def test_mobile_approval_websocket_rejects_query_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)

    try:
        with client.websocket_connect(f"/ws/mobile/approvals?token={token}"):
            raise AssertionError("Mobile approval WebSocket should reject URL query tokens")
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", None) == 1008


def test_mobile_approval_websocket_redacts_created_event(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        connected = websocket.receive_json()
        assert connected["type"] == "connected"

        approval = Approval(
            task_id="task_ws_mobile_secret",
            step_id="step_1",
            message="Approve from phone with token=secret-message-ws-1234567890",
            diff_preview={"value": "Authorization Bearer secret-token-1234567890"},
        )
        db.upsert_model("approvals", approval)
        publish_approval_created(approval)

        event = websocket.receive_json()

    event_text = json.dumps(event, ensure_ascii=False)
    assert event["type"] == "approval_created"
    assert "secret-token-1234567890" not in event_text
    assert "secret-message-ws-1234567890" not in event_text


def _paired_token(client: TestClient) -> str:
    code = client.post("/api/pair/code").json()["code"]
    return client.post("/api/pair", json={"code": code, "device_name": "Test Phone"}).json()["token"]


def _enable_remote_desktop() -> None:
    patch = {"remote_desktop_enabled": True}
    confirmation = create_settings_confirmation(patch)
    if confirmation.get("required"):
        patch["confirmation_nonce"] = confirmation["nonce"]
    update_settings(patch)


def _claim_remote_input_token(client: TestClient, paired_token: str, grant: dict) -> str:
    response = client.post(
        f"/api/mobile/remote-input-grants/{grant['grant_id']}/token",
        headers={"Authorization": f"Bearer {paired_token}"},
    )
    assert response.status_code == 200
    return response.json()["token"]


def _run_mobile_jwt_subprocess(script: str, data_dir: Path, extra_env: dict[str, str] | None = None) -> str:
    env = os.environ.copy()
    env.update(
        {
            "LENGRVIS_DATA_DIR": str(data_dir),
            "LENGRVIS_ENV_FILE": str(data_dir.parent / "missing.env"),
            "LENGRVIS_CONFIG_FILE": str(data_dir.parent / "missing.yaml"),
        }
    )
    env.pop("LENGRVIS_JWT_SECRET", None)
    env.pop("LENGRVIS_JWT_SECRET", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.check_output(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        text=True,
    ).strip()


def _clear_pairing_failures() -> None:
    with mobile_pairing_service._PAIR_CONFIRM_FAILURES_LOCK:
        mobile_pairing_service._PAIR_CONFIRM_FAILURES.clear()
