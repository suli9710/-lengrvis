from __future__ import annotations

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import db
from app.main import app


def test_remote_lan_client_can_redeem_but_not_create_pairing_codes_or_use_desktop_apis(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MARVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    db.init_db()
    loopback = TestClient(app, client=("127.0.0.1", 50100))
    remote = TestClient(app, client=("192.168.1.22", 50100))

    assert remote.post("/api/pair/code").status_code == 403
    assert remote.post("/api/pair/request").status_code == 403
    code_response = remote.post("/api/pair")
    assert code_response.status_code in {401, 422}
    assert remote.get("/api/tasks").status_code == 403

    code = loopback.post("/api/pair/code").json()["code"]
    pair_response = remote.post("/api/pair", json={"code": code, "device_name": "LAN phone"})
    assert pair_response.status_code == 200
    assert pair_response.json()["token"]


def test_remote_lan_client_cannot_open_desktop_task_websocket(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app, client=("192.168.1.22", 50100))

    try:
        with client.websocket_connect("/ws/tasks/task_1"):
            raise AssertionError("Remote desktop WebSocket should be blocked")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_loopback_client_keeps_desktop_api_access(monkeypatch, tmp_path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))

    assert client.post("/api/pair/code").status_code == 200
    assert client.get("/api/tasks").status_code == 200
