from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import db
from app.main import create_app
from app.security.desktop_api import DESKTOP_API_WS_PROTOCOL_PREFIX


DESKTOP_SECRET = "desktop-secret"


@pytest.mark.parametrize(
    ("headers", "subprotocols"),
    [
        ({}, []),
        ({"X-Mavris-Desktop-Token": "wrong-secret"}, []),
        ({}, [f"{DESKTOP_API_WS_PROTOCOL_PREFIX}wrong-secret"]),
        ({}, [DESKTOP_SECRET]),
    ],
    ids=["missing-token", "bad-header-token", "bad-subprotocol-token", "bare-subprotocol-token"],
)
def test_production_desktop_websocket_rejects_unauthorized_clients(monkeypatch, tmp_path, headers, subprotocols):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/tasks/task_ws_auth", headers=headers, subprotocols=subprotocols):
            raise AssertionError("unauthorized desktop websocket should be closed")

    assert exc_info.value.code == 1008
    assert exc_info.value.reason == "unauthorized"


def test_production_desktop_websocket_accepts_subprotocol_token(monkeypatch, tmp_path):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with client.websocket_connect(
        "/ws/tasks/task_ws_auth",
        subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"],
    ) as websocket:
        assert websocket.receive_json() == {"type": "connected", "task_id": "task_ws_auth"}


def test_production_desktop_websocket_accepts_prefixed_token_among_subprotocols(monkeypatch, tmp_path):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with client.websocket_connect(
        "/ws/tasks/task_ws_auth",
        subprotocols=["mavris.client.v1", f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"],
    ) as websocket:
        assert websocket.receive_json() == {"type": "connected", "task_id": "task_ws_auth"}


def _configure_production_desktop_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MAVRIS_DESKTOP_API_TOKEN", DESKTOP_SECRET)
    monkeypatch.delenv("MAVRIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("MARVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    db.init_db()
