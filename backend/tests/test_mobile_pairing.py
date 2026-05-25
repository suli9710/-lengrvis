from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core import db
from app.core.schemas import Approval
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


def _paired_token(client: TestClient) -> str:
    code = client.post("/api/pair/code").json()["code"]
    return client.post("/api/pair", json={"code": code, "device_name": "Test Phone"}).json()["token"]
