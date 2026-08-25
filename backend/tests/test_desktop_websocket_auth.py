from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core import db
from app.main import create_app
from app.security.desktop_api import DESKTOP_API_WS_PROTOCOL_PREFIX

DESKTOP_SECRET = "desktop-secret"  # noqa: S105 - deterministic test credential.


class _FakeWsUrl:
    def __init__(self) -> None:
        self.scheme = "ws"
        self.hostname = "127.0.0.1"
        self.port = 8000


class _FakeWebSocket:
    def __init__(self, origin: str) -> None:
        self.headers = {"origin": origin}
        self.url = _FakeWsUrl()


def test_vite_dev_origin_trusted_in_dev_but_not_release(monkeypatch):
    from app.security.websocket_origin import is_trusted_websocket_origin

    ws = _FakeWebSocket("http://localhost:5173")

    # Dev profile: the Vite dev-server origin is trusted (developer convenience).
    for name in ("LENGRVIS_ENV", "APP_ENV", "ENVIRONMENT", "LENGRVIS_RELEASE_CHANNEL"):
        monkeypatch.delenv(name, raising=False)
    for name in ("LENGRVIS_COMMERCIAL_RELEASE", "LENGRVIS_PUBLIC_BETA", "LENGRVIS_RELEASE_BUILD"):
        monkeypatch.delenv(name, raising=False)
    assert is_trusted_websocket_origin(ws) is True

    # Release profile: a local process squatting on :5173 must not pass the
    # Origin CSRF check.
    monkeypatch.setenv("LENGRVIS_ENV", "ga")
    assert is_trusted_websocket_origin(ws) is False


@pytest.mark.parametrize(
    ("headers", "subprotocols"),
    [
        ({}, []),
        ({"X-Lengrvis-Desktop-Token": "wrong-secret"}, []),
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
        subprotocols=["lengrvis.client.v1", f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"],
    ) as websocket:
        assert websocket.receive_json() == {"type": "connected", "task_id": "task_ws_auth"}


def test_production_desktop_websocket_rejects_untrusted_origin(monkeypatch, tmp_path):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/tasks/task_ws_auth",
            headers={"Origin": "https://evil.example"},
            subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"],
        ):
            raise AssertionError("desktop websocket should reject untrusted browser origins")

    assert exc_info.value.code == 1008


def test_strict_desktop_websocket_accepts_missing_origin_with_subprotocol_token(monkeypatch, tmp_path):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_STRICT_WEBSOCKET_ORIGIN", "true")
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with client.websocket_connect(
        "/ws/tasks/task_ws_auth",
        subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"],
    ) as websocket:
        assert websocket.receive_json() == {"type": "connected", "task_id": "task_ws_auth"}


def test_strict_desktop_websocket_rejects_missing_origin_without_token(monkeypatch, tmp_path):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_STRICT_WEBSOCKET_ORIGIN", "true")
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/tasks/task_ws_auth"):
            raise AssertionError("strict desktop websocket should reject missing Origin without token")

    assert exc_info.value.code == 1008


def test_strict_desktop_websocket_accepts_app_origin(monkeypatch, tmp_path):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_STRICT_WEBSOCKET_ORIGIN", "true")
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    with client.websocket_connect(
        "/ws/tasks/task_ws_auth",
        headers={"Origin": "app://local"},
        subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"],
    ) as websocket:
        assert websocket.receive_json() == {"type": "connected", "task_id": "task_ws_auth"}


def _configure_production_desktop_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", DESKTOP_SECRET)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    db.init_db()
