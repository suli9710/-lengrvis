from __future__ import annotations

from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api import routes_settings
from app.core import db
from app.core.schemas import Run, RunEngine, RunPhase
from app.main import app
from app.guardian import create_guardian_app
from app.security.desktop_api import DESKTOP_API_WS_PROTOCOL_PREFIX, signed_desktop_resource_query
from app.services import mobile_pairing_service
from tls_test_material import write_lan_tls_material


DESKTOP_SECRET = "desktop-secret"


def _enable_lan_tls(monkeypatch, tmp_path) -> None:
    cert, key = write_lan_tls_material(tmp_path)
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(key))


def test_remote_lan_client_can_redeem_but_not_create_pairing_codes_or_use_desktop_apis(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
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
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app, client=("192.168.1.22", 50100))

    try:
        with client.websocket_connect("/ws/tasks/task_1"):
            raise AssertionError("Remote desktop WebSocket should be blocked")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_remote_lan_client_cannot_open_guardian_catch_all_websocket_without_desktop_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    db.init_db()

    import app.api.routes_guardian as routes_guardian

    async def fail_if_proxied(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("unauthorized LAN WebSocket was proxied to the full backend")

    monkeypatch.setattr(routes_guardian.runtime, "ensure_full_backend", fail_if_proxied)
    client = TestClient(create_guardian_app(), client=("192.168.1.22", 50100))

    try:
        with client.websocket_connect("/ws/custom/full-backend"):
            raise AssertionError("Remote LAN catch-all WebSocket should require a desktop token")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_remote_lan_client_with_desktop_token_can_reach_guardian_catch_all_websocket(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    db.init_db()

    import app.api.routes_guardian as routes_guardian

    seen: dict[str, object] = {"proxied": False}

    async def fake_ensure_full_backend(*args, **kwargs):  # noqa: ANN002, ANN003
        seen["proxied"] = True
        raise RuntimeError("stop before upstream connection")

    monkeypatch.setattr(routes_guardian.runtime, "shell_mode", "foreground")
    monkeypatch.setattr(routes_guardian.runtime, "ensure_full_backend", fake_ensure_full_backend)

    client = TestClient(create_guardian_app(), client=("192.168.1.22", 50100))
    try:
        with client.websocket_connect(
            "/ws/custom/full-backend",
            subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}desktop-secret"],
        ):
            pass
    except WebSocketDisconnect as exc:
        assert exc.code == 1011
    assert seen["proxied"] is True


def test_guardian_http_proxy_rejects_mobile_and_remote_namespaces(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    db.init_db()

    import app.api.routes_guardian as routes_guardian

    async def fail_if_proxied(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("mobile/remote namespace should not be proxied to the full backend")

    monkeypatch.setattr(routes_guardian.runtime, "proxy", fail_if_proxied)
    client = TestClient(create_guardian_app(), client=("192.168.1.22", 50100))

    for path in (
        "/api/mobile",
        "/ws/mobile/approvals",
        "/ws/mobile",
        "/api/ws/mobile/approvals",
        "/api/ws/mobile",
        "/ws/remote/screen",
        "/ws/remote",
        "/api/ws/remote/screen",
        "/api/ws/remote",
        "/api/mobile/nonexistent",
    ):
        response = client.get(path)
        assert response.status_code in {401, 404}


def test_remote_lan_client_cannot_trigger_local_model_install_websocket(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app, client=("192.168.1.22", 50100))

    try:
        with client.websocket_connect("/ws/settings/install-local-model"):
            raise AssertionError("Remote model install WebSocket should be blocked")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


@pytest.mark.parametrize(
    ("path", "expected_connected"),
    [
        (
            "/ws/tasks/task_production_auth",
            {"type": "connected", "task_id": "task_production_auth"},
        ),
        (
            "/ws/runs/run_production_auth",
            {
                "type": "connected",
                "run_id": "run_production_auth",
                "engine": "os",
                "phase": "completed",
            },
        ),
        (
            "/ws/settings/install-local-model?model=qwen2.5:3b",
            {"phase": "pull", "status": "success", "model": "qwen2.5:3b"},
        ),
        (
            "/ws/notifications",
            {"type": "connected", "task_id": "__system__"},
        ),
    ],
    ids=["tasks", "runs", "install-local-model", "notifications"],
)
def test_loopback_desktop_websockets_require_subprotocol_token_in_production_config(
    monkeypatch,
    tmp_path,
    path,
    expected_connected,
):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    _prepare_desktop_websocket_target(monkeypatch)
    client = TestClient(app, client=("127.0.0.1", 50100))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(path):
            raise AssertionError("bare desktop WebSocket should require a desktop token")

    assert exc_info.value.code == 1008

    with client.websocket_connect(
        path,
        subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"],
    ) as websocket:
        assert websocket.receive_json() == expected_connected


def test_browser_host_websocket_requires_desktop_authorization(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", DESKTOP_SECRET)
    db.init_db()
    remote = TestClient(app, client=("192.168.1.22", 50100))

    try:
        with remote.websocket_connect("/api/ws/browser-host"):
            raise AssertionError("Remote browser host websocket should require desktop authorization")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008

    with remote.websocket_connect(
        "/api/ws/browser-host",
        headers={"Sec-WebSocket-Protocol": f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"},
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"


def test_loopback_client_keeps_desktop_api_access(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_TEST", "1")
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", "1")
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))

    assert client.post("/api/pair/code").status_code == 200
    assert client.get("/api/tasks").status_code == 200


def test_desktop_get_requires_desktop_token_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    db.init_db()
    remote = TestClient(app, client=("192.168.1.22", 50100))
    loopback = TestClient(app, client=("127.0.0.1", 50100))

    blocked = remote.get("/api/tasks")
    allowed = remote.get("/api/tasks", headers={"X-Lengrvis-Desktop-Token": "desktop-secret"})

    assert blocked.status_code == 401
    assert allowed.status_code == 200
    assert loopback.get("/api/tasks").status_code == 401
    assert loopback.get("/api/tasks", headers={"X-Lengrvis-Desktop-Token": "desktop-secret"}).status_code == 200


def test_loopback_state_changes_require_desktop_token_when_configured(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))

    blocked = client.post("/api/pair/code")
    allowed = client.post("/api/pair/code", headers={"X-Lengrvis-Desktop-Token": "desktop-secret"})
    redeem = client.post("/api/pair", json={"code": allowed.json()["code"], "device_name": "Phone"})

    assert blocked.status_code == 401
    assert allowed.status_code == 200
    assert redeem.status_code == 200


def test_remote_input_grant_creation_requires_desktop_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    db.init_db()
    import app.services.mobile_pairing_service as pairing_module

    monkeypatch.setattr(pairing_module, "get_effective_settings", lambda: type("Settings", (), {"remote_desktop_enabled": True})())
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_input_guard", device_name="Guarded Phone")
    client = TestClient(app, client=("127.0.0.1", 50100))

    blocked = client.post("/api/pair/devices/mobile_input_guard/remote-input-grants")
    allowed = client.post(
        "/api/pair/devices/mobile_input_guard/remote-input-grants",
        headers={"X-Lengrvis-Desktop-Token": "desktop-secret"},
    )

    assert blocked.status_code == 401
    assert allowed.status_code == 200
    payload = allowed.json()
    assert payload["grant_id"]
    assert payload["device_id"] == "mobile_input_guard"
    assert "token" not in payload
    assert "token_type" not in payload


def test_loopback_state_changes_require_persisted_desktop_token_by_default(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DEV", raising=False)
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))

    blocked = client.post("/api/pair/code")
    blocked_read = client.get("/api/tasks")
    token = (tmp_path / "desktop_api.secret").read_text(encoding="utf-8").strip()
    allowed = client.post("/api/pair/code", headers={"X-Lengrvis-Desktop-Token": token})
    allowed_read = client.get("/api/tasks", headers={"X-Lengrvis-Desktop-Token": token})

    assert blocked.status_code == 401
    assert blocked_read.status_code == 401
    assert len(token) >= 32
    assert allowed.status_code == 200
    assert allowed_read.status_code == 200


def test_signed_desktop_resource_is_bound_to_http_method(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOWED_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", DESKTOP_SECRET)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    image_path = tmp_path / "preview.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))
    query = urlencode(
        {
            "path": str(image_path),
            **signed_desktop_resource_query("/api/library/preview", str(image_path), method="GET"),
        }
    )

    assert client.get(f"/api/library/preview?{query}").status_code == 200
    assert client.post(f"/api/library/preview?{query}").status_code == 401


def test_lengrvis_dev_does_not_disable_desktop_token_guard(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    monkeypatch.setenv("LENGRVIS_DEV", "1")
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))

    blocked = client.post("/api/pair/code")
    token = (tmp_path / "desktop_api.secret").read_text(encoding="utf-8").strip()
    allowed = client.post("/api/pair/code", headers={"X-Lengrvis-Desktop-Token": token})

    assert blocked.status_code == 401
    assert allowed.status_code == 200


def _configure_production_desktop_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", DESKTOP_SECRET)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    db.init_db()


def _prepare_desktop_websocket_target(monkeypatch) -> None:
    run = Run(
        id="run_production_auth",
        message="completed run for websocket auth test",
        requested_engine=RunEngine.OS,
        engine=RunEngine.OS,
        phase=RunPhase.COMPLETED,
    )
    db.upsert_model("runs", run)

    async def fake_install_local_model(model=None):
        yield {"phase": "pull", "status": "success", "model": model or "qwen2.5:3b"}

    monkeypatch.setattr(routes_settings.ollama_service, "install_local_model", fake_install_local_model)
