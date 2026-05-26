from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import Approval, Plan, PlanStep, Task
from app.main import app
from app.security.mobile_jwt import decode_mobile_token
from app.services.approval_event_service import publish_approval_created


def test_pair_request_generates_code(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)

    response = client.post("/api/pair/request")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["code"]) == 6
    int(payload["code"], 16)
    assert payload["expires_in"] <= 300


def test_pair_confirm_valid_code(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)

    response = client.get("/api/mobile/approvals/pending")

    assert response.status_code == 401


def test_pair_code_can_be_redeemed_once_for_mobile_jwt(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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


def test_mobile_approval_routes_require_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)

    response = client.get("/api/mobile/approvals/pending")

    assert response.status_code == 401


def test_mobile_can_list_and_decide_pending_approvals(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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


def test_mobile_approval_payload_redacts_sensitive_preview(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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


def test_mobile_detail_redacts_task_and_omits_plan_args(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)

    with client.websocket_connect(f"/ws/mobile/approvals?token={token}") as websocket:
        connected = websocket.receive_json()
        assert connected["type"] == "connected"

        approval = Approval(task_id="task_ws_mobile", step_id="step_1", message="Approve from phone")
        db.upsert_model("approvals", approval)
        publish_approval_created(approval)

        event = websocket.receive_json()

    assert event["type"] == "approval_created"
    assert event["approval"]["id"] == approval.id


def test_mobile_approval_websocket_redacts_created_event(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app)
    token = _paired_token(client)

    with client.websocket_connect(f"/ws/mobile/approvals?token={token}") as websocket:
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
