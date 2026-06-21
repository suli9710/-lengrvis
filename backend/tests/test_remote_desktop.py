from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from app.api import routes_remote
from app.core import db
from app.llm.registry import get_effective_settings
from app.policy.permissions import PermissionPolicy, PermissionRule, PermissionStore
from app.policy.risk import RiskLevel
from app.security import mobile_jwt
from app.security.sensitive_confirmation import create_settings_confirmation
from app.security.mobile_jwt import MOBILE_AUTH_WS_PROTOCOL_PREFIX, REMOTE_VIEW_SCOPE, TOKEN_SCOPE, issue_mobile_token
from app.services import mobile_pairing_service, remote_desktop_service
from app.services.settings_service import update_settings
from app.tools.registry import register_all_tools

REMOTE_WS_RETRY_CLOSE_CODE = 1012
REMOTE_WS_AUTH_CLOSE_CODE = 4401
REMOTE_WS_GRANT_CLOSE_CODE = 4403


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    db.init_db()
    yield

def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_remote.ws_router)
    app.include_router(routes_remote.ws_router, prefix="/api")
    return app

def _mobile_token() -> str:
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_test", device_name="Test Phone")
    return issue_mobile_token(device_id="mobile_test", device_name="Test Phone")

def _scoped_mobile_token(scope: str) -> str:
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_test", device_name="Test Phone")
    return issue_mobile_token(device_id="mobile_test", device_name="Test Phone", scope=scope)

def _short_lived_scoped_mobile_token(scope: str) -> str:
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_test", device_name="Test Phone")
    return issue_mobile_token(device_id="mobile_test", device_name="Test Phone", scope=scope, expires_in_seconds=60)

def _remote_input_grant_token(device_id: str, device_name: str = "Input Phone") -> tuple[str, str]:
    mobile_pairing_service._upsert_mobile_device(device_id=device_id, device_name=device_name)
    grant = mobile_pairing_service.create_remote_input_grant(device_id)
    claimed = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": device_id, "device_name": device_name},
    )
    return claimed["token"], grant["grant_id"]

def _enable_remote_desktop() -> None:
    patch = {"remote_desktop_enabled": True}
    confirmation = create_settings_confirmation(patch)
    if confirmation.get("required"):
        patch["confirmation_nonce"] = confirmation["nonce"]
    update_settings(patch)

def _assert_no_sensitive_details(payload: dict, fragments: list[str]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False).replace("\\\\", "\\")
    for fragment in fragments:
        assert fragment not in serialized

def _expire_remote_input_grant(device_id: str, grant_id: str) -> None:
    device = db.fetch_one("mobile_devices", device_id)
    assert device is not None
    matched = False
    for grant in device.get("remote_input_grants") or []:
        if grant.get("id") == grant_id:
            matched = True
            grant["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert matched is True
    with db.connect() as conn:
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), device_id),
        )

def test_remote_screen_capture_failure_sends_generic_error(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    raw_error = (
        r"capture failed at C:\Users\Suli\Desktop\secrets\screen.txt "
        "token=secretREMOTE123456 selector=#password-field "
        "hostname=screen-host.internal.local "
        r'Traceback (most recent call last): File "C:\Users\Suli\Desktop\lengrvis\backend\app\api\routes_remote.py", line 117, in capture'
    )

    def fail_capture(**kwargs):
        raise RuntimeError(raw_error)

    monkeypatch.setattr(routes_remote, "capture_screen_frame", fail_capture)
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        error = websocket.receive_json()

    assert error == {
        "type": "error",
        "code": "remote_screen.capture_failed",
        "message": "Remote screen is temporarily unavailable.",
    }

def test_remote_input_policy_failure_sends_generic_rejection_and_redacted_audit(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    device_id = "mobile_input_policy_secret_device"
    token, grant_id = _remote_input_grant_token(device_id, "Policy Host")
    raw_error = (
        rf"policy failed at C:\Users\Suli\Desktop\secrets\policy.txt "
        rf"token=secretPOLICY123456 selector=#policy-secret hostname=policy.internal.local "
        rf"device_id={device_id} grant_id={grant_id} "
        r'Traceback (most recent call last): File "C:\Users\Suli\Desktop\lengrvis\backend\app\policy\policy_engine.py", line 201, in review'
    )

    def fail_policy(event, *, claims=None):
        raise HTTPException(status_code=403, detail=raw_error)

    monkeypatch.setattr(routes_remote, "handle_remote_input_event", fail_policy)
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        error = websocket.receive_json()

    assert error == {
        "type": "error",
        "code": "remote_input.rejected",
        "message": "Remote input event was rejected.",
        "status_code": 403,
    }

def test_remote_input_unexpected_exception_sends_generic_error(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    raw_error = (
        r"input crashed at C:\Users\Suli\Desktop\secrets\input.txt "
        "token=secretINPUT123456 selector=#api-key "
        "hostname=input-host.internal.local "
        r'Traceback (most recent call last): File "C:\Users\Suli\Desktop\lengrvis\backend\app\api\routes_remote.py", line 201, in input'
    )

    def fail_input(event, *, claims=None):
        raise RuntimeError(raw_error)

    monkeypatch.setattr(routes_remote, "handle_remote_input_event", fail_input)
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_input_error", device_name="Input Phone")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_input_error")
    claimed = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_input_error", "device_name": "Input Phone"},
    )
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{claimed['token']}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        error = websocket.receive_json()

    assert error == {
        "type": "error",
        "code": "remote_input.failed",
        "message": "Remote input event could not be handled.",
        "status_code": 500,
    }
