from __future__ import annotations

from urllib.parse import urlencode

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tls_test_material import write_lan_tls_material

from app.api import routes_settings
from app.core import db
from app.core.schemas import Run, RunEngine, RunPhase
from app.guardian import create_guardian_app
from app.main import app
from app.security.desktop_api import (
    DESKTOP_API_TOKEN_FILE,
    DESKTOP_API_WS_PROTOCOL_PREFIX,
    desktop_api_token,
    signed_desktop_resource_query,
)
from app.security.local_secret import (
    LOCAL_SECRET_DPAPI_PREFIX,
    LOCAL_SECRET_KEYRING_PREFIX,
    dpapi_available,
    keyring_available,
)
from app.security.mobile_jwt import (
    MOBILE_AUTH_WS_PROTOCOL_PREFIX,
    REMOTE_VIEW_SCOPE,
    TOKEN_SCOPE,
    issue_mobile_token,
)
from app.security.sensitive_confirmation import create_settings_confirmation
from app.services import mobile_pairing_service
from app.services.settings_service import update_settings

DESKTOP_SECRET = "desktop-secret"  # noqa: S105 - deterministic test credential.


def _enable_lan_tls(monkeypatch, tmp_path) -> None:
    cert, key = write_lan_tls_material(tmp_path)
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(key))


def _enable_remote_desktop() -> None:
    patch = {"remote_desktop_enabled": True}
    confirmation = create_settings_confirmation(patch)
    if confirmation.get("required"):
        patch["confirmation_nonce"] = confirmation["nonce"]
    update_settings(patch)


def test_remote_lan_client_needs_https_for_mobile_token_paths(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    db.init_db()
    loopback = TestClient(app, client=("127.0.0.1", 50100))
    remote = TestClient(app, client=("192.168.1.22", 50100))
    secure_remote = TestClient(app, client=("192.168.1.22", 50100), base_url="https://testserver")

    assert remote.post("/api/pair/code").status_code == 403
    assert remote.post("/api/pair/request").status_code == 403
    code_response = remote.post("/api/pair")
    assert code_response.status_code == 403
    assert remote.get("/api/tasks").status_code == 403

    pairing = loopback.post("/api/pair/code").json()
    pair_response = remote.post(
        "/api/pair",
        json={"code": pairing["code"], "claim_secret": pairing["claim_secret"], "device_name": "LAN phone"},
    )
    assert pair_response.status_code == 403

    pairing = loopback.post("/api/pair/code").json()
    pair_response = secure_remote.post(
        "/api/pair",
        json={"code": pairing["code"], "claim_secret": pairing["claim_secret"], "device_name": "LAN phone"},
    )
    assert pair_response.status_code == 200
    token = pair_response.json()["token"]
    assert token
    pairing = loopback.post("/api/pair/code").json()
    assert (
        remote.post(
            "/api/pair/confirm",
            json={"code": pairing["code"], "claim_secret": pairing["claim_secret"], "device_name": "LAN phone"},
        ).status_code
        == 403
    )
    confirm_response = secure_remote.post(
        "/api/pair/confirm",
        json={"code": pairing["code"], "claim_secret": pairing["claim_secret"], "device_name": "LAN phone"},
    )
    assert confirm_response.status_code == 200
    assert remote.get("/api/mobile/devices", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert secure_remote.get("/api/mobile/devices", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_guardian_remote_lan_client_needs_https_for_mobile_token_paths(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    db.init_db()
    guardian = create_guardian_app()
    loopback = TestClient(guardian, client=("127.0.0.1", 50100))
    remote = TestClient(guardian, client=("192.168.1.22", 50100))
    secure_remote = TestClient(guardian, client=("192.168.1.22", 50100), base_url="https://testserver")

    assert remote.post("/api/pair/code").status_code == 403
    assert remote.post("/api/pair/request").status_code == 403
    pairing = loopback.post("/api/pair/code").json()
    assert (
        remote.post(
            "/api/pair",
            json={
                "code": pairing["code"],
                "claim_secret": pairing["claim_secret"],
                "device_name": "Guardian LAN phone",
            },
        ).status_code
        == 403
    )

    pairing = loopback.post("/api/pair/code").json()
    pair_response = secure_remote.post(
        "/api/pair",
        json={"code": pairing["code"], "claim_secret": pairing["claim_secret"], "device_name": "Guardian LAN phone"},
    )
    assert pair_response.status_code == 200
    token = pair_response.json()["token"]
    assert remote.get("/api/mobile/devices", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert secure_remote.get("/api/mobile/devices", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_remote_lan_client_cannot_open_desktop_task_websocket(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    client = TestClient(app, client=("192.168.1.22", 50100))

    try:
        with client.websocket_connect("/ws/tasks/task_1"):
            raise AssertionError("Remote desktop WebSocket should be blocked")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008


def test_remote_lan_mobile_and_remote_websockets_require_wss(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_lan_ws", device_name="LAN Phone")
    mobile_token = issue_mobile_token(
        device_id="mobile_lan_ws",
        device_name="LAN Phone",
        scope=[TOKEN_SCOPE, REMOTE_VIEW_SCOPE],
    )
    grant = mobile_pairing_service.create_remote_input_grant("mobile_lan_ws")
    remote_input_token = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_lan_ws", "device_name": "LAN Phone"},
    )["token"]
    client = TestClient(app, client=("192.168.1.22", 50100))
    loopback_client = TestClient(app, client=("127.0.0.1", 50100))

    for path, token in (
        ("/ws/mobile/approvals", mobile_token),
        ("/api/ws/mobile/approvals", mobile_token),
        ("/ws/remote/screen", mobile_token),
        ("/api/ws/remote/input", remote_input_token),
    ):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                path,
                subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
            ):
                raise AssertionError(f"Remote LAN WebSocket {path} should require WSS")
        assert exc_info.value.code == 1008

    with client.websocket_connect(
        "wss://testserver/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{mobile_token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
    with client.websocket_connect(
        "wss://testserver/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{remote_input_token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
    with loopback_client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{mobile_token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
    with loopback_client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{remote_input_token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"


def test_mobile_websockets_reject_untrusted_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    _enable_remote_desktop()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_origin_ws", device_name="Origin Phone")
    mobile_token = issue_mobile_token(
        device_id="mobile_origin_ws",
        device_name="Origin Phone",
        scope=[TOKEN_SCOPE, REMOTE_VIEW_SCOPE],
    )
    client = TestClient(app, client=("127.0.0.1", 50100))

    for path in ("/ws/mobile/approvals", "/ws/remote/screen"):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                path,
                headers={"Origin": "https://evil.example"},
                subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{mobile_token}"],
            ):
                raise AssertionError(f"mobile websocket {path} should reject untrusted browser origins")
        assert exc_info.value.code == 1008


def test_strict_mobile_websocket_allows_missing_origin_only_with_token_protocol(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_STRICT_WEBSOCKET_ORIGIN", "true")
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_strict_ws", device_name="Strict Phone")
    mobile_token = issue_mobile_token(
        device_id="mobile_strict_ws",
        device_name="Strict Phone",
        scope=[TOKEN_SCOPE],
    )
    client = TestClient(app, client=("127.0.0.1", 50100))

    with client.websocket_connect(
        "/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{mobile_token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/mobile/approvals"):
            raise AssertionError("strict mobile websocket should reject missing origin without token proof")

    assert exc_info.value.code == 1008


def test_guardian_remote_lan_mobile_and_remote_websockets_are_not_desktop_proxied(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    mobile_pairing_service._upsert_mobile_device(device_id="guardian_lan_ws", device_name="Guardian LAN Phone")
    mobile_token = issue_mobile_token(
        device_id="guardian_lan_ws",
        device_name="Guardian LAN Phone",
        scope=[TOKEN_SCOPE],
    )
    import app.api.routes_guardian as routes_guardian

    async def fail_if_proxied(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("Guardian mobile/remote WebSocket namespace should not proxy to the full backend")

    monkeypatch.setattr(routes_guardian.runtime, "ensure_full_backend", fail_if_proxied)
    guardian = create_guardian_app()
    client = TestClient(guardian, client=("192.168.1.22", 50100))

    for path in ("/ws/mobile/approvals", "/api/ws/mobile/approvals"):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                path,
                subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{mobile_token}"],
            ):
                raise AssertionError(f"Guardian mobile WebSocket {path} should require WSS")
        assert exc_info.value.code == 1008

    with client.websocket_connect(
        "wss://testserver/ws/mobile/approvals",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{mobile_token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"

    for path in ("/ws/remote/screen", "/api/ws/remote/screen", "/ws/remote/input", "/api/ws/remote/input"):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(path):
                raise AssertionError(f"Guardian remote WebSocket {path} should be reserved and closed")
        assert exc_info.value.code == 1008


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
            raise AssertionError("Remote LAN catch-all WebSocket should require WSS")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008

    try:
        with client.websocket_connect(
            "wss://testserver/ws/custom/full-backend",
            subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}desktop-secret"],
        ):
            pass
    except WebSocketDisconnect as exc:
        assert exc.code == 1011
    assert seen["proxied"] is True


def test_guardian_mobile_ws_roots_are_not_desktop_proxied(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    db.init_db()

    import app.api.routes_guardian as routes_guardian

    seen: dict[str, object] = {"proxied": False}

    async def fail_if_proxied(*args, **kwargs):  # noqa: ANN002, ANN003
        seen["proxied"] = True
        raise AssertionError("mobile/remote WebSocket roots should not be proxied to the full backend")

    monkeypatch.setattr(routes_guardian.runtime, "shell_mode", "foreground")
    monkeypatch.setattr(routes_guardian.runtime, "ensure_full_backend", fail_if_proxied)
    client = TestClient(create_guardian_app(), client=("192.168.1.22", 50100))

    for path in ("/ws/mobile", "/api/ws/mobile", "/ws/remote", "/api/ws/remote"):
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                path,
                subprotocols=[f"{DESKTOP_API_WS_PROTOCOL_PREFIX}desktop-secret"],
            ):
                raise AssertionError(f"{path} should be reserved for mobile transport")
        assert exc_info.value.code == 1008

    assert seen["proxied"] is False


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
        assert response.status_code in {401, 403, 404}


def test_guardian_lan_desktop_proxy_requires_https_with_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", DESKTOP_SECRET)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    db.init_db()

    import app.api.routes_guardian as routes_guardian

    proxied: list[str] = []

    async def fake_proxy(method, path, **kwargs):  # noqa: ANN001, ANN202, ANN003
        proxied.append(f"{method} {path}")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(routes_guardian.runtime, "proxy", fake_proxy)
    app_under_test = create_guardian_app()
    remote = TestClient(app_under_test, client=("192.168.1.22", 50100))
    secure_remote = TestClient(app_under_test, client=("192.168.1.22", 50100), base_url="https://testserver")

    assert remote.get("/api/tasks", headers={"X-Lengrvis-Desktop-Token": DESKTOP_SECRET}).status_code == 403
    assert secure_remote.get("/api/tasks").status_code == 401
    allowed = secure_remote.get("/api/tasks", headers={"X-Lengrvis-Desktop-Token": DESKTOP_SECRET})

    assert allowed.status_code == 200
    assert allowed.json() == {"ok": True}
    assert proxied == ["GET /api/tasks"]


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
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    db.init_db()
    remote = TestClient(app, client=("192.168.1.22", 50100))

    try:
        with remote.websocket_connect("/api/ws/browser-host"):
            raise AssertionError("Remote browser host websocket should require desktop authorization")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with remote.websocket_connect(
            "/api/ws/browser-host",
            headers={"Sec-WebSocket-Protocol": f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"},
        ):
            raise AssertionError("Remote browser host websocket should require LAN desktop API opt-in")
    assert exc_info.value.code == 1008

    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with remote.websocket_connect(
            "/api/ws/browser-host",
            headers={"Sec-WebSocket-Protocol": f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}"},
        ):
            raise AssertionError("Remote browser host websocket should require WSS")
    assert exc_info.value.code == 1008

    with remote.websocket_connect(
        "wss://testserver/api/ws/browser-host",
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
    db.init_db()
    remote = TestClient(app, client=("192.168.1.22", 50100))
    secure_remote = TestClient(app, client=("192.168.1.22", 50100), base_url="https://testserver")
    loopback = TestClient(app, client=("127.0.0.1", 50100))

    blocked = remote.get("/api/tasks")
    insecure_token = remote.get("/api/tasks", headers={"X-Lengrvis-Desktop-Token": "desktop-secret"})
    secure_missing_token = secure_remote.get("/api/tasks")
    allowed = secure_remote.get("/api/tasks", headers={"X-Lengrvis-Desktop-Token": "desktop-secret"})

    assert blocked.status_code == 401
    assert insecure_token.status_code == 403
    assert secure_missing_token.status_code == 401
    assert allowed.status_code == 200
    assert loopback.get("/api/tasks").status_code == 401
    assert loopback.get("/api/tasks", headers={"X-Lengrvis-Desktop-Token": "desktop-secret"}).status_code == 200


def test_untrusted_forwarded_headers_do_not_bypass_loopback_lan_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    monkeypatch.delenv("LENGRVIS_TRUSTED_PROXY_IPS", raising=False)
    db.init_db()
    proxied = TestClient(app, client=("127.0.0.1", 50100))

    response = proxied.get(
        "/api/tasks",
        headers={
            "X-Lengrvis-Desktop-Token": "desktop-secret",
            "X-Forwarded-For": "192.168.1.22",
            "X-Forwarded-Proto": "http",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "untrusted_proxy_headers"


def test_trusted_forwarded_headers_restore_remote_https_lan_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    monkeypatch.setenv("LENGRVIS_TRUSTED_PROXY_IPS", "127.0.0.1")
    db.init_db()
    proxied = TestClient(app, client=("127.0.0.1", 50100))
    headers = {
        "X-Lengrvis-Desktop-Token": "desktop-secret",
        "X-Forwarded-For": "192.168.1.22",
    }

    insecure = proxied.get("/api/tasks", headers={**headers, "X-Forwarded-Proto": "http"})
    secure = proxied.get("/api/tasks", headers={**headers, "X-Forwarded-Proto": "https"})

    assert insecure.status_code == 403
    assert secure.status_code == 200


def test_trusted_forwarded_headers_ignore_spoofed_leftmost_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    monkeypatch.delenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", raising=False)
    monkeypatch.setenv("LENGRVIS_TRUSTED_PROXY_IPS", "127.0.0.1")
    db.init_db()
    proxied = TestClient(app, client=("127.0.0.1", 50100))

    response = proxied.get(
        "/api/tasks",
        headers={
            "X-Lengrvis-Desktop-Token": "desktop-secret",
            "X-Forwarded-For": "127.0.0.1, 192.168.1.22",
            "X-Forwarded-Proto": "https",
        },
    )

    assert response.status_code == 403


def test_untrusted_forwarded_headers_do_not_bypass_loopback_desktop_websocket(monkeypatch, tmp_path):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    _prepare_desktop_websocket_target(monkeypatch)
    monkeypatch.delenv("LENGRVIS_TRUSTED_PROXY_IPS", raising=False)
    client = TestClient(app, client=("127.0.0.1", 50100))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/tasks/task_production_auth",
            headers={
                "X-Forwarded-For": "192.168.1.22",
                "X-Forwarded-Proto": "http",
                "Sec-WebSocket-Protocol": f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}",
            },
        ):
            raise AssertionError("Untrusted forwarded headers should not be treated as loopback")

    assert exc_info.value.code == 1008


def test_trusted_forwarded_headers_restore_remote_wss_desktop_websocket(monkeypatch, tmp_path):
    _configure_production_desktop_token(monkeypatch, tmp_path)
    _prepare_desktop_websocket_target(monkeypatch)
    monkeypatch.setenv("LENGRVIS_ALLOW_LAN_DESKTOP_API", "1")
    monkeypatch.setenv("LENGRVIS_TRUSTED_PROXY_IPS", "127.0.0.1")
    client = TestClient(app, client=("127.0.0.1", 50100))

    with client.websocket_connect(
        "/ws/tasks/task_production_auth",
        headers={
            "X-Forwarded-For": "192.168.1.22",
            "X-Forwarded-Proto": "wss",
            "Sec-WebSocket-Protocol": f"{DESKTOP_API_WS_PROTOCOL_PREFIX}{DESKTOP_SECRET}",
        },
    ) as websocket:
        assert websocket.receive_json() == {"type": "connected", "task_id": "task_production_auth"}


def test_loopback_state_changes_require_desktop_token_when_configured(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))

    blocked = client.post("/api/pair/code")
    allowed = client.post("/api/pair/code", headers={"X-Lengrvis-Desktop-Token": "desktop-secret"})
    pairing = allowed.json()
    redeem = client.post(
        "/api/pair",
        json={"code": pairing["code"], "claim_secret": pairing["claim_secret"], "device_name": "Phone"},
    )

    assert blocked.status_code == 401
    assert allowed.status_code == 200
    assert redeem.status_code == 200


def test_remote_input_grant_creation_requires_desktop_token(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-secret")
    db.init_db()
    import app.services.mobile_pairing_service as pairing_module

    settings = type("Settings", (), {"remote_desktop_enabled": True})()
    monkeypatch.setattr(pairing_module, "get_effective_settings", lambda: settings)
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


def test_desktop_api_token_is_persisted_with_secure_backend_when_available(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN", raising=False)

    token = desktop_api_token()
    stored = (tmp_path / DESKTOP_API_TOKEN_FILE).read_text(encoding="utf-8").strip()

    assert token
    assert len(token) >= 32
    if dpapi_available():
        assert stored.startswith(LOCAL_SECRET_DPAPI_PREFIX)
        assert token not in stored
    elif keyring_available():
        assert stored.startswith(LOCAL_SECRET_KEYRING_PREFIX)
        assert token not in stored
    else:
        assert stored == token


def test_loopback_state_changes_require_persisted_desktop_token_by_default(monkeypatch, tmp_path):
    _enable_lan_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN", raising=False)
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_DEV", raising=False)
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))

    blocked = client.post("/api/pair/code")
    blocked_read = client.get("/api/tasks")
    token = desktop_api_token()
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
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    monkeypatch.delenv("LENGRVIS_TEST", raising=False)
    monkeypatch.setenv("LENGRVIS_DEV", "1")
    db.init_db()
    client = TestClient(app, client=("127.0.0.1", 50100))

    blocked = client.post("/api/pair/code")
    token = desktop_api_token()
    allowed = client.post("/api/pair/code", headers={"X-Lengrvis-Desktop-Token": token})

    assert blocked.status_code == 401
    assert allowed.status_code == 200


def test_missing_client_host_is_not_treated_as_loopback():
    from app.security.lan import is_loopback_host

    assert is_loopback_host(None) is False
    assert is_loopback_host("") is False
    assert is_loopback_host("   ") is False
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("192.168.1.22") is False


def _configure_production_desktop_token(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", DESKTOP_SECRET)
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
