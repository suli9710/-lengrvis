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
from app.security.mobile_jwt import MOBILE_AUTH_WS_PROTOCOL_PREFIX, REMOTE_VIEW_SCOPE, issue_mobile_token
from app.services import mobile_pairing_service, remote_desktop_service
from app.services.settings_service import update_settings
from app.tools.registry import register_all_tools


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


def test_capture_screen_returns_base64(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (1600, 900), "red"))

    image = remote_desktop_service.capture_screen()

    assert isinstance(image, str)
    assert len(image) > 100
    assert image.startswith("/9j/")


def test_remote_tools_require_approval():
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)

    assert registry.get("remote.view_screen").risk_level == RiskLevel.R1_OPEN_ONLY
    for tool_name in ("remote.click", "remote.type_text", "remote.key_press"):
        tool = registry.get(tool_name)
        assert tool.risk_level == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM
        assert tool.supports_dry_run is True

    _enable_remote_desktop()
    enabled = {"settings": get_effective_settings(), "allowed_directories": []}
    result = registry.get("remote.click").execute({"x": 1, "y": 2, "dry_run": False}, enabled)
    assert result["ok"] is False
    assert "approval_id" in result["error"]


def test_remote_disabled_by_default():
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{_mobile_token()}"],
        ):
            raise AssertionError("Remote desktop WebSocket should be disabled by default")

    assert exc_info.value.code == 1008


def test_remote_view_token_cannot_open_remote_screen_after_remote_desktop_disabled(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)
    update_settings({"remote_desktop_enabled": False})
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ):
            raise AssertionError("Remote view token should not open screen after remote desktop is disabled")

    assert exc_info.value.code == 1008


def test_mobile_approval_token_cannot_open_remote_screen():
    _enable_remote_desktop()
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{_mobile_token()}"],
        ):
            raise AssertionError("Approval-scoped token should not open remote screen")

    assert exc_info.value.code == 1008


def test_remote_input_scope_cannot_open_remote_screen():
    _enable_remote_desktop()
    client = TestClient(_test_app())
    token, _grant_id = _remote_input_grant_token("mobile_input_screen_boundary")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ):
            raise AssertionError("Remote input token should not open remote screen")

    assert exc_info.value.code == 1008


def test_remote_view_scope_cannot_open_remote_input():
    _enable_remote_desktop()
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/input",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ):
            raise AssertionError("Remote view token should not open remote input")

    assert exc_info.value.code == 1008


def test_remote_view_scope_can_open_remote_screen(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        frame = websocket.receive_json()
        assert frame["type"] == "frame"
        assert frame["sequence"] == 1


def test_remote_screen_sends_next_frame_after_ack(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        first = websocket.receive_json()
        assert first["type"] == "frame"
        assert first["sequence"] == 1

        websocket.send_json({"type": "frame_ack", "sequence": 1, "fps": 5, "quality": 42})
        second = websocket.receive_json()

    assert second["type"] == "frame"
    assert second["sequence"] == 2


def test_remote_screen_rejects_query_token(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/remote/screen?token={token}"):
            raise AssertionError("Remote screen should reject URL query tokens")

    assert exc_info.value.code == 1008


def test_remote_view_scope_can_open_remote_screen_with_token_subprotocol(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        frame = websocket.receive_json()
        assert frame["type"] == "frame"
        assert frame["sequence"] == 1


def test_remote_screen_invalid_control_sends_generic_error(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)
    raw_control = (
        r'{"type": "frame_ack", "sequence": token=secretCONTROL123456 '
        r"selector=#admin hostname=screen-control.internal.local "
        r"C:\Users\Suli\Desktop\secrets\control.txt"
    )

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        frame = websocket.receive_json()
        assert frame["type"] == "frame"
        websocket.send_text(raw_control)
        error = websocket.receive_json()

    assert error == {
        "type": "error",
        "code": "remote_screen.invalid_control",
        "message": "Invalid screen stream control message.",
    }
    _assert_no_sensitive_details(
        error,
        [
            r"C:\Users\Suli",
            "secretCONTROL123456",
            "#admin",
            "screen-control.internal.local",
            "JSONDecodeError",
            "line 1 column",
        ],
    )


def test_remote_screen_capture_failure_sends_generic_error(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    raw_error = (
        r"capture failed at C:\Users\Suli\Desktop\secrets\screen.txt "
        "token=secretREMOTE123456 selector=#password-field "
        "hostname=screen-host.internal.local "
        r'Traceback (most recent call last): File "C:\Users\Suli\Desktop\mavris\backend\app\api\routes_remote.py", line 117, in capture'
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
    _assert_no_sensitive_details(
        error,
        [
            r"C:\Users\Suli",
            "secretREMOTE123456",
            "#password-field",
            "screen-host.internal.local",
            "capture failed",
            "RuntimeError",
            "Traceback",
            "line 117",
            "mobile_test",
            "Test Phone",
        ],
    )
    failure = next(
        event
        for event in db.fetch_many("audit_events", limit=20)
        if event["event_type"] == "remote.screen.capture_failed"
    )
    _assert_no_sensitive_details(
        failure["payload"],
        [
            r"C:\Users\Suli",
            "secretREMOTE123456",
            "#password-field",
            "screen-host.internal.local",
            "Traceback",
            "line 117",
            "mobile_test",
            "Test Phone",
        ],
    )
    assert "[REDACTED" in json.dumps(failure["payload"], ensure_ascii=False)


def test_revoked_remote_view_token_cannot_open_remote_screen(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)
    mobile_pairing_service.revoke_mobile_device("mobile_test")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ):
            raise AssertionError("Revoked token should not open remote screen")

    assert exc_info.value.code == 1008


def test_connected_remote_screen_closes_after_device_revoked(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        mobile_pairing_service.revoke_mobile_device("mobile_test")
        close_code = None
        for _ in range(4):
            try:
                websocket.receive_json()
            except WebSocketDisconnect as exc:
                close_code = exc.code
                break

    assert close_code == 1008


def test_connected_remote_screen_closes_after_token_expires(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _short_lived_scoped_mobile_token(REMOTE_VIEW_SCOPE)

    class ExpiredTokenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=120)

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        first = websocket.receive_json()
        assert first["type"] == "frame"
        monkeypatch.setattr(mobile_jwt, "datetime", ExpiredTokenClock)
        websocket.send_json({"type": "frame_ack", "sequence": first["sequence"]})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_connected_remote_screen_closes_after_remote_desktop_disabled(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        first = websocket.receive_json()
        assert first["type"] == "frame"
        update_settings({"remote_desktop_enabled": False})
        websocket.send_json({"type": "frame_ack", "sequence": first["sequence"]})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_input_events_audited(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)

    result = routes_remote.handle_remote_input_event(
        {"type": "click", "x": 100, "y": 200},
        claims={"device_id": "mobile_test", "device_name": "Test Phone", "sub": "mobile:mobile_test"},
    )

    assert result["type"] == "approval_required"
    events = db.fetch_many("audit_events", limit=20)
    assert any(event["event_type"] == "remote.input.received" for event in events)
    assert any(event["event_type"] == "remote.input.approval_requested" for event in events)


def test_remote_input_policy_deny_uses_generic_client_and_summarized_audit():
    _enable_remote_desktop()
    raw_reason = (
        r"deny remote input at C:\Users\Suli\Desktop\secrets\deny.txt "
        "token=secretDENYREASON123456 selector=#deny-secret "
        "hostname=deny-policy.internal.local"
    )
    PermissionStore().save_policy(
        PermissionPolicy(
            rules=[
                PermissionRule(
                    id="perm_remote_input_deny",
                    name="Remote input deny",
                    effect="deny",
                    tools=["remote.click"],
                    reason=raw_reason,
                )
            ]
        )
    )
    device_id = "mobile_input_deny_secret_device"
    token, grant_id = _remote_input_grant_token(device_id, "Deny Host")
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        denial = websocket.receive_json()

    assert denial["type"] == "denied"
    assert denial["code"] == "remote_input.denied"
    assert denial["message"] == "Remote input event was denied by policy."
    assert denial["status_code"] == 403
    assert denial["task_id"].startswith("task_")
    assert "reasons" not in denial

    sensitive_fragments = [
        r"C:\Users\Suli",
        "secretDENYREASON123456",
        "#deny-secret",
        "deny-policy.internal.local",
        raw_reason,
        device_id,
        grant_id,
        "Deny Host",
    ]
    _assert_no_sensitive_details(denial, sensitive_fragments)
    events = db.fetch_many("audit_events", limit=20)
    for event in events:
        _assert_no_sensitive_details(event["payload"], sensitive_fragments)
    denied_event = next(event for event in events if event["event_type"] == "remote.input.denied")

    assert "reasons" not in denied_event["payload"]
    assert denied_event["payload"]["reason_count"] == 1
    summary = denied_event["payload"]["reason_summaries"][0]
    assert summary["summary"] == "Policy denied remote input; reason details redacted."
    assert summary["sensitive_categories"] == ["hostname", "local_path", "secret_or_token", "selector"]
    assert len(summary["digest"]) == 12
    assert all(char in "0123456789abcdef" for char in summary["digest"])


def test_remote_input_unsupported_event_sends_generic_rejection_and_redacted_audit():
    _enable_remote_desktop()
    device_id = "mobile_input_unsupported_secret_device"
    token, grant_id = _remote_input_grant_token(device_id, "Unsupported Host")
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json(
            {
                "type": "hover",
                "selector": "#secret-field",
                "token": "secretUNSUPPORTED123456",
                "hostname": "unsupported.internal.local",
                "device_id": device_id,
                "grant_id": grant_id,
            }
        )
        error = websocket.receive_json()

    assert error == {
        "type": "error",
        "code": "remote_input.rejected",
        "message": "Remote input event was rejected.",
        "status_code": 400,
    }
    _assert_no_sensitive_details(
        error,
        [
            "Unsupported remote input event",
            "#secret-field",
            "secretUNSUPPORTED123456",
            "unsupported.internal.local",
            device_id,
            grant_id,
            "Unsupported Host",
            "HTTPException",
        ],
    )
    failure = next(
        event
        for event in db.fetch_many("audit_events", limit=20)
        if event["event_type"] == "remote.input.rejected"
    )
    _assert_no_sensitive_details(
        failure["payload"],
        [
            "#secret-field",
            "secretUNSUPPORTED123456",
            "unsupported.internal.local",
            device_id,
            grant_id,
            "Unsupported Host",
        ],
    )
    assert "[REDACTED" in json.dumps(failure["payload"], ensure_ascii=False)


def test_remote_input_policy_failure_sends_generic_rejection_and_redacted_audit(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    device_id = "mobile_input_policy_secret_device"
    token, grant_id = _remote_input_grant_token(device_id, "Policy Host")
    raw_error = (
        rf"policy failed at C:\Users\Suli\Desktop\secrets\policy.txt "
        rf"token=secretPOLICY123456 selector=#policy-secret hostname=policy.internal.local "
        rf"device_id={device_id} grant_id={grant_id} "
        r'Traceback (most recent call last): File "C:\Users\Suli\Desktop\mavris\backend\app\policy\policy_engine.py", line 201, in review'
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
    client_sensitive_fragments = [
        r"C:\Users\Suli",
        "secretPOLICY123456",
        "#policy-secret",
        "policy.internal.local",
        device_id,
        grant_id,
        "Policy Host",
        "policy failed",
        "HTTPException",
        "Traceback",
        "line 201",
    ]
    audit_sensitive_fragments = [
        fragment
        for fragment in client_sensitive_fragments
        if fragment not in {"policy failed", "HTTPException"}
    ]
    _assert_no_sensitive_details(error, client_sensitive_fragments)
    failure = next(
        event
        for event in db.fetch_many("audit_events", limit=20)
        if event["event_type"] == "remote.input.rejected"
    )
    _assert_no_sensitive_details(failure["payload"], audit_sensitive_fragments)
    assert "[REDACTED" in json.dumps(failure["payload"], ensure_ascii=False)


def test_remote_input_unexpected_exception_sends_generic_error(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    raw_error = (
        r"input crashed at C:\Users\Suli\Desktop\secrets\input.txt "
        "token=secretINPUT123456 selector=#api-key "
        "hostname=input-host.internal.local "
        r'Traceback (most recent call last): File "C:\Users\Suli\Desktop\mavris\backend\app\api\routes_remote.py", line 201, in input'
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
    _assert_no_sensitive_details(
        error,
        [
            r"C:\Users\Suli",
            "secretINPUT123456",
            "#api-key",
            "input-host.internal.local",
            "input crashed",
            "RuntimeError",
            "Traceback",
            "line 201",
            "mobile_input_error",
            "Input Phone",
        ],
    )
    failure = next(
        event
        for event in db.fetch_many("audit_events", limit=20)
        if event["event_type"] == "remote.input.failed"
    )
    _assert_no_sensitive_details(
        failure["payload"],
        [
            r"C:\Users\Suli",
            "secretINPUT123456",
            "#api-key",
            "input-host.internal.local",
            "Traceback",
            "line 201",
            "mobile_input_error",
            grant["grant_id"],
            "Input Phone",
        ],
    )
    assert "[REDACTED" in json.dumps(failure["payload"], ensure_ascii=False)


def test_connected_remote_input_closes_after_grant_revoked(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_input", device_name="Input Phone")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_input")
    claimed = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_input", "device_name": "Input Phone"},
    )
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{claimed['token']}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        mobile_pairing_service.revoke_remote_input_grant("mobile_input", grant["grant_id"])
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_idle_remote_input_closes_after_grant_revoked(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_input_idle", device_name="Input Phone")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_input_idle")
    claimed = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_input_idle", "device_name": "Input Phone"},
    )
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{claimed['token']}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        mobile_pairing_service.revoke_remote_input_grant("mobile_input_idle", grant["grant_id"])
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_connected_remote_input_closes_after_grant_expires(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_input_expiring", device_name="Input Phone")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_input_expiring")
    claimed = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_input_expiring", "device_name": "Input Phone"},
    )
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{claimed['token']}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        _expire_remote_input_grant("mobile_input_expiring", grant["grant_id"])
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_connected_remote_input_closes_after_token_expires(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_input_token_expiring", device_name="Input Phone")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_input_token_expiring")
    token = issue_mobile_token(
        device_id="mobile_input_token_expiring",
        device_name="Input Phone",
        scope=mobile_jwt.REMOTE_INPUT_SCOPE,
        source="remote_input_grant",
        grant_id=grant["grant_id"],
        expires_in_seconds=60,
    )
    client = TestClient(_test_app())

    class ExpiredTokenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=120)

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        monkeypatch.setattr(mobile_jwt, "datetime", ExpiredTokenClock)
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_idle_remote_input_closes_after_token_expires(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_input_idle_token_expiring", device_name="Input Phone")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_input_idle_token_expiring")
    token = issue_mobile_token(
        device_id="mobile_input_idle_token_expiring",
        device_name="Input Phone",
        scope=mobile_jwt.REMOTE_INPUT_SCOPE,
        source="remote_input_grant",
        grant_id=grant["grant_id"],
        expires_in_seconds=60,
    )
    client = TestClient(_test_app())

    class ExpiredTokenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=120)

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        monkeypatch.setattr(mobile_jwt, "datetime", ExpiredTokenClock)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008


def test_idle_remote_input_closes_after_remote_desktop_disabled(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_input_disabled", device_name="Input Phone")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_input_disabled")
    claimed = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_input_disabled", "device_name": "Input Phone"},
    )
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{claimed['token']}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        update_settings({"remote_desktop_enabled": False})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1008
