from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from app.api import routes_remote
from app.core import db
from app.core.schemas import Approval, ApprovalStatus
from app.llm.registry import get_effective_settings
from app.policy.approval_binding import args_binding_hmac
from app.policy.execution_marker import mark_execution_approved
from app.policy.permissions import PermissionPolicy, PermissionRule, PermissionStore
from app.policy.risk import RiskLevel
from app.security import mobile_jwt
from app.security.mobile_jwt import MOBILE_AUTH_WS_PROTOCOL_PREFIX, REMOTE_VIEW_SCOPE, TOKEN_SCOPE, issue_mobile_token
from app.security.sensitive_confirmation import create_settings_confirmation
from app.services import mobile_pairing_service, remote_desktop_service
from app.services.remote_input_rate_limit import RemoteInputRateLimiter, RemoteInputRateLimiterStore
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
    routes_remote._REMOTE_INPUT_RATE_LIMITERS.clear()
    yield
    routes_remote._REMOTE_INPUT_RATE_LIMITERS.clear()


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
    _seed_remote_frame(device_id=device_id, grant_id=grant["grant_id"])
    return claimed["token"], grant["grant_id"]


def _seed_remote_frame(*, device_id: str, grant_id: str = "") -> None:
    routes_remote._remember_remote_screen_frame(
        {"device_id": device_id, "grant_id": grant_id},
        sequence=1,
        origin_x=0,
        origin_y=0,
        width=800,
        height=600,
    )


def _enable_remote_desktop() -> None:
    patch = {"remote_desktop_enabled": True}
    confirmation = create_settings_confirmation(patch)
    if confirmation.get("required"):
        patch["confirmation_nonce"] = confirmation["nonce"]
    update_settings(patch)


def _disable_remote_desktop() -> None:
    patch = {"remote_desktop_enabled": False}
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
            grant["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert matched is True
    with db.connect() as conn:
        conn.execute(
            "UPDATE mobile_devices SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(device, ensure_ascii=False), datetime.now(UTC).isoformat(), device_id),
        )


def test_capture_screen_returns_base64(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (1600, 900), "red"))

    image = remote_desktop_service.capture_screen()

    assert isinstance(image, str)
    assert len(image) > 100
    assert image.startswith("/9j/")


def test_capture_screen_frame_includes_virtual_desktop_origin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (1600, 900), "red"))
    monkeypatch.setattr(remote_desktop_service, "_virtual_screen_origin", lambda: (-1920, -120))

    frame = remote_desktop_service.capture_screen_frame()

    assert frame.original_width == 1600
    assert frame.original_height == 900
    assert frame.screen_origin_x == -1920
    assert frame.screen_origin_y == -120


def _live_remote_click_approval(x: int = 1, y: int = 2) -> Approval:
    device_id = "mobile_remote_live_click"
    mobile_pairing_service._upsert_mobile_device(device_id=device_id, device_name="Live Click Phone")
    grant = mobile_pairing_service.create_remote_input_grant(device_id)
    task_id = f"task_{device_id}"
    step_id = "step_1"
    tool_name = "remote.click"
    approval = Approval(
        task_id=task_id,
        step_id=step_id,
        approval_type="remote_input",
        message="Approve remote click",
        status=ApprovalStatus.APPROVED,
        source="remote_input",
        tool_name=tool_name,
        source_device_id=device_id,
        source_grant_id=grant["grant_id"],
        required_mobile_scopes=[mobile_jwt.REMOTE_INPUT_SCOPE],
    )
    approval.args_binding_hmac = args_binding_hmac(
        tool_name,
        {"x": x, "y": y},
        task_id=task_id,
        step_id=step_id,
    )
    db.upsert_model("approvals", approval, status=approval.status)
    return approval


def test_remote_tools_require_approval():
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)

    for tool_name in ("remote.view_screen", "remote.click", "remote.type_text", "remote.key_press"):
        tool = registry.get(tool_name)
        assert tool.supports_dry_run is True
    assert registry.get("remote.view_screen").risk_level == RiskLevel.R2_REVERSIBLE_MODIFY
    for tool_name in ("remote.click", "remote.type_text", "remote.key_press"):
        assert registry.get(tool_name).risk_level == RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM

    _enable_remote_desktop()
    enabled = {"settings": get_effective_settings(), "allowed_directories": []}
    screen = registry.get("remote.view_screen").execute({"quality": 50, "dry_run": False}, enabled)
    assert screen["ok"] is False
    assert "approval_id" in screen["error"]
    result = registry.get("remote.click").execute({"x": 1, "y": 2, "dry_run": False}, enabled)
    assert result["ok"] is False
    assert "approval_id" in result["error"]


def test_remote_tools_require_entitled_plan_even_with_enabled_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LENGRVIS_PLAN", "free")
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    enabled_settings = get_effective_settings().model_copy(update={"remote_desktop_enabled": True, "plan": "free"})
    enabled = {"settings": enabled_settings, "allowed_directories": []}

    result = registry.get("remote.view_screen").execute({"quality": 50, "dry_run": True}, enabled)

    assert result["ok"] is False
    assert "disabled" in result["error"].lower()


def test_remote_tools_reject_forged_live_approval(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    enabled = {"settings": get_effective_settings(), "allowed_directories": []}
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr("app.tools.remote_tools._click_at", lambda x, y: calls.append((x, y)))

    result = registry.get("remote.click").execute(
        {"x": 1, "y": 2, "dry_run": False, "approved": True, "approval_id": "fake-approval"},
        enabled,
    )

    assert result["ok"] is False
    assert "approval_id" in result["error"]
    assert calls == []


def test_remote_tools_execute_after_orchestrator_claim(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    enabled = {"settings": get_effective_settings(), "allowed_directories": []}
    approval = _live_remote_click_approval()
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr("app.tools.remote_tools._click_at", lambda x, y: calls.append((x, y)))
    db.claim_approval_for_execution(approval.id, datetime.now(UTC).isoformat())
    mark_execution_approved(enabled)

    result = registry.get("remote.click").execute(
        {"x": 1, "y": 2, "dry_run": False, "approved": True, "approval_id": approval.id},
        enabled,
    )

    assert result["ok"] is True
    assert calls == [(1, 2)]


def test_remote_tools_reject_replay_without_execution_marker(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    enabled = {"settings": get_effective_settings(), "allowed_directories": []}
    approval = _live_remote_click_approval()
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr("app.tools.remote_tools._click_at", lambda x, y: calls.append((x, y)))
    db.claim_approval_for_execution(approval.id, datetime.now(UTC).isoformat())

    result = registry.get("remote.click").execute(
        {"x": 1, "y": 2, "dry_run": False, "approved": True, "approval_id": approval.id},
        enabled,
    )

    assert result["ok"] is False
    assert "approval_id" in result["error"]
    assert calls == []


def test_remote_tools_direct_path_claims_before_execution(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    enabled = {"settings": get_effective_settings(), "allowed_directories": []}
    approval = _live_remote_click_approval()
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr("app.tools.remote_tools._click_at", lambda x, y: calls.append((x, y)))

    result = registry.get("remote.click").execute(
        {"x": 1, "y": 2, "dry_run": False, "approved": True, "approval_id": approval.id},
        enabled,
    )

    assert result["ok"] is True
    assert calls == [(1, 2)]
    refreshed = db.fetch_one("approvals", approval.id)
    assert refreshed is not None
    assert refreshed.get("consumed_at")


def test_remote_tools_direct_path_rejects_double_execution(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    enabled = {"settings": get_effective_settings(), "allowed_directories": []}
    approval = _live_remote_click_approval()
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr("app.tools.remote_tools._click_at", lambda x, y: calls.append((x, y)))
    click_tool = registry.get("remote.click")
    live_args = {"x": 1, "y": 2, "dry_run": False, "approved": True, "approval_id": approval.id}

    first = click_tool.execute(live_args, enabled)
    second = click_tool.execute(live_args, enabled)

    assert first["ok"] is True
    assert second["ok"] is False
    assert "approval_id" in second["error"]
    assert calls == [(1, 2)]


def test_remote_disabled_by_default():
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{_mobile_token()}"],
        ):
            raise AssertionError("Remote desktop WebSocket should be disabled by default")

    assert exc_info.value.code == REMOTE_WS_RETRY_CLOSE_CODE


def test_remote_view_token_cannot_open_remote_screen_after_remote_desktop_disabled(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)
    _disable_remote_desktop()
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ):
            raise AssertionError("Remote view token should not open screen after remote desktop is disabled")

    assert exc_info.value.code == REMOTE_WS_RETRY_CLOSE_CODE


def test_remote_view_token_cannot_open_remote_screen_without_entitled_plan(monkeypatch: pytest.MonkeyPatch):
    free_enabled_settings = get_effective_settings().model_copy(update={"remote_desktop_enabled": True, "plan": "free"})
    monkeypatch.setattr(routes_remote, "get_effective_settings", lambda: free_enabled_settings)
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ):
            raise AssertionError("Remote view token should not open screen without an entitled plan")

    assert exc_info.value.code == REMOTE_WS_RETRY_CLOSE_CODE


def test_mobile_approval_token_cannot_open_remote_screen():
    _enable_remote_desktop()
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/screen",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{_mobile_token()}"],
        ):
            raise AssertionError("Approval-scoped token should not open remote screen")

    assert exc_info.value.code == REMOTE_WS_GRANT_CLOSE_CODE


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

    assert exc_info.value.code == REMOTE_WS_GRANT_CLOSE_CODE


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

    assert exc_info.value.code == REMOTE_WS_GRANT_CLOSE_CODE


def test_mobile_approval_token_cannot_open_remote_input():
    _enable_remote_desktop()
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/input",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{_mobile_token()}"],
        ):
            raise AssertionError("Approval-scoped token should not open remote input")

    assert exc_info.value.code == REMOTE_WS_GRANT_CLOSE_CODE


def test_plain_remote_input_scope_without_grant_cannot_open_remote_input():
    _enable_remote_desktop()
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_plain_input", device_name="Plain Input")
    token = issue_mobile_token(
        device_id="mobile_plain_input",
        device_name="Plain Input",
        scope=mobile_jwt.REMOTE_INPUT_SCOPE,
    )
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/ws/remote/input",
            subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
        ):
            raise AssertionError("Remote input scope without a grant should not open remote input")

    assert exc_info.value.code == REMOTE_WS_GRANT_CLOSE_CODE


def test_remote_view_scope_can_open_remote_screen(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    monkeypatch.setattr(remote_desktop_service, "_virtual_screen_origin", lambda: (-1920, -120))
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
        assert frame["screen_origin_x"] == -1920
        assert frame["screen_origin_y"] == -120


def test_pairing_token_with_mobile_and_remote_view_scope_can_open_remote_screen(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_view_pair", device_name="View Pair")
    token = issue_mobile_token(
        device_id="mobile_view_pair",
        device_name="View Pair",
        scope=[TOKEN_SCOPE, REMOTE_VIEW_SCOPE],
    )
    client = TestClient(_test_app())

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
    monkeypatch.setattr(remote_desktop_service, "_virtual_screen_origin", lambda: (-1920, -120))
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
    assert second["screen_origin_x"] == -1920
    assert second["screen_origin_y"] == -120


def test_remote_screen_rejects_query_token(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/remote/screen?token={token}"):
            raise AssertionError("Remote screen should reject URL query tokens")

    assert exc_info.value.code == REMOTE_WS_AUTH_CLOSE_CODE


def test_remote_input_rejects_query_token():
    _enable_remote_desktop()
    client = TestClient(_test_app())
    token, _grant_id = _remote_input_grant_token("mobile_input_query_token")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/remote/input?token={token}"):
            raise AssertionError("Remote input should reject URL query tokens")

    assert exc_info.value.code == REMOTE_WS_AUTH_CLOSE_CODE


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
        r"C:\\Users\\Suli\\Desktop\\secrets\\control.txt"
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
            r"C:\\Users\\Suli",
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
        r"capture failed at C:\\Users\\Suli\\Desktop\\secrets\\screen.txt "
        "token=secretREMOTE123456 selector=#password-field "
        "hostname=screen-host.internal.local "
        "Traceback (most recent call last): File "
        r'"C:\\Users\\Suli\\Desktop\\lengrvis\\backend\\app\\api\\routes_remote.py", line 117, in capture'
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
            r"C:\\Users\\Suli",
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
            r"C:\\Users\\Suli",
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

    assert exc_info.value.code == REMOTE_WS_AUTH_CLOSE_CODE


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

    assert close_code == REMOTE_WS_AUTH_CLOSE_CODE


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

    assert exc_info.value.code == REMOTE_WS_AUTH_CLOSE_CODE


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
        _disable_remote_desktop()
        websocket.send_json({"type": "frame_ack", "sequence": first["sequence"]})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == REMOTE_WS_RETRY_CLOSE_CODE


def test_connected_remote_screen_closes_after_plan_loses_remote_view(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(remote_desktop_service, "_grab_screen", lambda: Image.new("RGB", (100, 100), "blue"))
    entitled_settings = get_effective_settings().model_copy(update={"remote_desktop_enabled": True, "plan": "pro"})
    free_settings = entitled_settings.model_copy(update={"plan": "free"})
    current_settings = {"value": entitled_settings}
    monkeypatch.setattr(routes_remote, "get_effective_settings", lambda: current_settings["value"])
    client = TestClient(_test_app())
    token = _scoped_mobile_token(REMOTE_VIEW_SCOPE)

    with client.websocket_connect(
        "/ws/remote/screen",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        first = websocket.receive_json()
        assert first["type"] == "frame"
        current_settings["value"] = free_settings
        websocket.send_json({"type": "frame_ack", "sequence": first["sequence"]})
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == REMOTE_WS_RETRY_CLOSE_CODE


def test_input_events_audited(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    claims = {"device_id": "mobile_test", "device_name": "Test Phone", "sub": "mobile:mobile_test"}
    _seed_remote_frame(device_id="mobile_test")

    result = routes_remote.handle_remote_input_event(
        {"type": "click", "x": 100, "y": 200},
        claims=claims,
    )

    assert result["type"] == "approval_required"
    events = db.fetch_many("audit_events", limit=20)
    assert any(event["event_type"] == "remote.input.received" for event in events)
    assert any(event["event_type"] == "remote.input.approval_requested" for event in events)


def test_remote_key_input_rejects_unsafe_key_before_approval(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    raw_key = r"token=abc123 selector=#password C:\\Users\\Suli\\secret.txt"
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)

    with pytest.raises(HTTPException):
        routes_remote.handle_remote_input_event(
            {"type": "key", "key": raw_key},
            claims={"device_id": "mobile_key_audit", "device_name": "Key Phone", "grant_id": "rig_key_audit"},
        )

    assert db.fetch_many("approvals", limit=20) == []


@pytest.mark.parametrize(
    ("event", "tool_name", "preview", "expected_args"),
    [
        (
            {"type": "type", "text": "hello from phone"},
            "remote.type_text",
            {"ok": True, "dry_run": True, "diff_preview": [{"action": "type_text", "characters": 16}]},
            {"text": "hello from phone"},
        ),
        (
            {"type": "key", "key": "pagedown"},
            "remote.key_press",
            {"ok": True, "dry_run": True, "diff_preview": [{"action": "key_press", "key": "pagedown"}]},
            {"key": "pagedown"},
        ),
    ],
)
def test_remote_input_websocket_accepts_text_and_key_events(
    monkeypatch: pytest.MonkeyPatch,
    event: dict[str, object],
    tool_name: str,
    preview: dict[str, object],
    expected_args: dict[str, object],
):
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    calls: list[dict[str, object]] = []

    def fake_execute(args: dict[str, object], context: dict[str, object]) -> dict[str, object]:  # noqa: ARG001
        calls.append(dict(args))
        return preview

    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get(tool_name), "execute", fake_execute)
    token, grant_id = _remote_input_grant_token(f"mobile_{tool_name.replace('.', '_')}", "Input Phone")
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json(event)
        result = websocket.receive_json()

    assert result["type"] == "approval_required"
    assert calls == [{**expected_args, "dry_run": True}]
    approval = db.fetch_one("approvals", result["approval_id"])
    assert approval is not None
    assert approval["approval_type"] == "remote_input"
    assert approval["tool_name"] == tool_name
    assert approval["source_grant_id"] == grant_id
    assert approval["required_mobile_scopes"] == [mobile_jwt.REMOTE_INPUT_SCOPE]
    assert approval["diff_preview"] == {
        "ok": True,
        "dry_run": True,
        "message": "Remote desktop input preview. User approval is required before execution.",
        "diff_preview": preview["diff_preview"],
    }


def test_remote_input_rejects_oversized_text_before_approval():
    _enable_remote_desktop()

    with pytest.raises(HTTPException):
        routes_remote.handle_remote_input_event(
            {"type": "type", "text": "x" * 181},
            claims={"device_id": "mobile_input_oversized", "grant_id": "rig_oversized"},
        )

    assert db.fetch_many("approvals", limit=20) == []


def test_remote_input_rejects_key_outside_server_allowlist_before_approval():
    _enable_remote_desktop()

    with pytest.raises(HTTPException):
        routes_remote.handle_remote_input_event(
            {"type": "key", "key": "delete"},
            claims={"device_id": "mobile_input_key_boundary", "grant_id": "rig_key_boundary"},
        )

    assert db.fetch_many("approvals", limit=20) == []


def test_remote_input_rejects_click_outside_latest_frame_before_approval():
    _enable_remote_desktop()
    claims = {"device_id": "mobile_input_click_boundary", "grant_id": "rig_click_boundary"}
    routes_remote._remember_remote_screen_frame(
        claims,
        sequence=1,
        origin_x=10,
        origin_y=20,
        width=100,
        height=80,
    )

    with pytest.raises(HTTPException):
        routes_remote.handle_remote_input_event({"type": "click", "x": 111, "y": 40}, claims=claims)

    assert db.fetch_many("approvals", limit=20) == []


def test_remote_input_rejects_click_without_recent_frame_before_approval():
    _enable_remote_desktop()

    with pytest.raises(HTTPException):
        routes_remote.handle_remote_input_event(
            {"type": "click", "x": 10, "y": 40},
            claims={"device_id": "mobile_input_click_no_frame", "grant_id": "rig_click_no_frame"},
        )

    assert db.fetch_many("approvals", limit=20) == []


def test_remote_input_websocket_rate_limits_event_burst(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(routes_remote, "_REMOTE_INPUT_MAX_EVENTS_PER_WINDOW", 1)
    monkeypatch.setattr(routes_remote, "_REMOTE_INPUT_RATE_LIMIT_WINDOW_SECONDS", 60.0)
    calls: list[dict[str, object]] = []

    def fake_handle(event: dict[str, object], *, claims: dict[str, object] | None = None) -> dict[str, object]:
        calls.append({"event": event, "claims": claims or {}})
        return {"type": "accepted"}

    monkeypatch.setattr(routes_remote, "handle_remote_input_event", fake_handle)
    token, _grant_id = _remote_input_grant_token("mobile_input_rate_limit", "Rate Limit Host")
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        assert websocket.receive_json() == {"type": "accepted"}
        websocket.send_json({"type": "click", "x": 101, "y": 201})
        assert websocket.receive_json() == {
            "type": "error",
            "code": "remote_input.rate_limited",
            "message": "Remote input rate limit exceeded.",
            "status_code": 429,
        }
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()

    assert len(calls) == 1
    assert any(event["event_type"] == "remote.input.rate_limited" for event in db.fetch_many("audit_events", limit=20))


def test_remote_input_rate_limit_is_shared_by_grant_and_device(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    monkeypatch.setattr(routes_remote, "_REMOTE_INPUT_MAX_EVENTS_PER_WINDOW", 1)
    monkeypatch.setattr(routes_remote, "_REMOTE_INPUT_RATE_LIMIT_WINDOW_SECONDS", 60.0)
    monkeypatch.setattr(routes_remote, "_REMOTE_INPUT_RATE_LIMITERS", RemoteInputRateLimiterStore())
    calls: list[dict[str, object]] = []

    def fake_handle(event: dict[str, object], *, claims: dict[str, object] | None = None) -> dict[str, object]:
        calls.append({"event": event, "claims": claims or {}})
        return {"type": "accepted"}

    monkeypatch.setattr(routes_remote, "handle_remote_input_event", fake_handle)
    token, _grant_id = _remote_input_grant_token("mobile_input_shared_limit", "Shared Limit Host")
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        assert websocket.receive_json() == {"type": "accepted"}

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "click", "x": 101, "y": 201})
        assert websocket.receive_json() == {
            "type": "error",
            "code": "remote_input.rate_limited",
            "message": "Remote input rate limit exceeded.",
            "status_code": 429,
        }
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()

    assert len(calls) == 1


def test_remote_input_rate_limiters_prune_idle_grant_device_entries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(routes_remote, "_REMOTE_INPUT_RATE_LIMIT_WINDOW_SECONDS", 10.0)
    store = RemoteInputRateLimiterStore()
    monkeypatch.setattr(routes_remote, "_REMOTE_INPUT_RATE_LIMITERS", store)
    claims = {"grant_id": "grant_pruned", "device_id": "device_pruned"}
    fallback = RemoteInputRateLimiter()

    assert routes_remote._remote_input_limit_error(claims, fallback, now=100.0) is None
    assert ("grant_pruned", "device_pruned") in store.keys()

    routes_remote._remote_input_rate_limiter_for_claims(
        {"grant_id": "grant_active", "device_id": "device_active"},
        fallback,
        now=121.0,
    )

    assert ("grant_pruned", "device_pruned") not in store.keys()
    assert ("grant_active", "device_active") in store.keys()


def test_remote_input_websocket_pending_approval_limit_fails_closed(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    device_id = "mobile_input_pending_limit"
    token, grant_id = _remote_input_grant_token(device_id, "Pending Limit Host")
    db.upsert_model(
        "approvals",
        Approval(
            task_id="task_remote_pending_limit",
            step_id="step_1",
            approval_type="remote_input",
            message="Existing pending remote input",
            source="remote_input",
            source_device_id=device_id,
            source_grant_id=grant_id,
        ),
    )
    monkeypatch.setattr(routes_remote, "_REMOTE_INPUT_PENDING_APPROVAL_LIMIT", 1)
    calls: list[dict[str, object]] = []

    def fake_handle(event: dict[str, object], *, claims: dict[str, object] | None = None) -> dict[str, object]:
        calls.append({"event": event, "claims": claims or {}})
        return {"type": "accepted"}

    monkeypatch.setattr(routes_remote, "handle_remote_input_event", fake_handle)
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{token}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        assert websocket.receive_json() == {
            "type": "error",
            "code": "remote_input.rate_limited",
            "message": "Remote input rate limit exceeded.",
            "status_code": 429,
        }
        with pytest.raises(WebSocketDisconnect):
            websocket.receive_json()

    assert calls == []
    rate_limited = [
        event for event in db.fetch_many("audit_events", limit=20) if event["event_type"] == "remote.input.rate_limited"
    ]
    assert rate_limited
    assert rate_limited[0]["payload"]["reason"] == "pending_approvals"


def test_remote_input_approval_exposes_safe_mobile_metadata_without_sensitive_preview(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    private_text = "please type my private recovery phrase"
    device_id = "mobile_input_metadata_secret_device"
    grant_id = "grant_metadata_secret_123456"
    preview = {
        "ok": True,
        "dry_run": True,
        "message": (
            r"Remote preview at C:\\Users\\Suli\\Desktop\\secrets\\remote-input.txt "
            "token=secretPREVIEW123456 selector=#password hostname=metadata.internal.local"
        ),
        "diff_preview": [
            {
                "action": "type_text",
                "characters": len(private_text),
                "text": private_text,
                "selector": "#password",
                "token": "secretPREVIEW123456",
                "host": "metadata.internal.local",
                "path": r"C:\\Users\\Suli\\Desktop\\secrets\\remote-input.txt",
                "device_id": device_id,
                "grant_id": grant_id,
            }
        ],
        "_internal": {"device_id": device_id, "grant_id": grant_id},
    }
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.type_text"), "execute", lambda args, context: preview)

    result = routes_remote.handle_remote_input_event(
        {"type": "type", "text": private_text},
        claims={
            "device_id": device_id,
            "device_name": "Metadata Host",
            "grant_id": grant_id,
            "sub": f"mobile:{device_id}",
        },
    )

    assert result["type"] == "approval_required"
    approval = db.fetch_one("approvals", result["approval_id"])
    assert approval is not None
    mobile_payload = mobile_pairing_service.safe_approval_payload(approval)

    assert mobile_payload["approval_type"] == "remote_input"
    assert mobile_payload["tool_trust_tier"] == "system"
    assert "remote_screen" in mobile_payload["resource_kinds"]
    assert "desktop_ui" in mobile_payload["resource_kinds"]
    assert mobile_payload["tool_effects"] == ["type", "write"]
    binding = mobile_payload["remote_input_binding"]
    assert binding["device_bound"] is True
    assert binding["grant_bound"] is True
    assert binding["requires_remote_input_scope"] is True
    assert binding["binding_ref"].startswith("[remote-input-binding:")
    assert grant_id not in binding["binding_ref"]
    assert "source_device_id" not in mobile_payload
    assert "source_grant_id" not in mobile_payload
    assert "allowed_device_ids" not in mobile_payload
    assert mobile_payload["dry_run_summary"] == (
        f"Remote desktop dry-run: type {len(private_text)} character(s) into the focused control."
    )
    assert mobile_payload["diff_preview"] == {
        "ok": True,
        "dry_run": True,
        "message": "Remote desktop input preview. User approval is required before execution.",
        "diff_preview": [{"action": "type_text", "characters": len(private_text)}],
    }
    boundary = mobile_payload["engineering_boundary"]
    assert boundary["source"] == "remote_input"
    assert boundary["remote_input"] == {
        "event_type": "type",
        "requires_active_grant": True,
        "required_mobile_scopes": ["remote:input"],
        "device_binding": "active grant",
        "grant_binding": "active grant",
    }
    assert boundary["tool"]["name"] == "remote.type_text"
    assert boundary["tool"]["risk_level"] == "r3 destructive or system"
    assert boundary["tool"]["supports_dry_run"] is True
    assert boundary["dry_run"]["verified"] is True
    assert boundary["dry_run"]["action"] == "type_text"
    assert boundary["dry_run"]["summary"] == mobile_payload["dry_run_summary"]
    assert boundary["policy"]["verdict"] == "needs_user_approval"
    assert boundary["binding"] == {
        "args_bound": True,
        "preview_bound": True,
        "settings_bound": True,
        "permission_policy_bound": True,
    }

    mobile_metadata = {
        "result_preview": result["preview"],
        "diff_preview": mobile_payload["diff_preview"],
        "resource_kinds": mobile_payload["resource_kinds"],
        "tool_effects": mobile_payload["tool_effects"],
        "dry_run_summary": mobile_payload["dry_run_summary"],
        "engineering_boundary": mobile_payload["engineering_boundary"],
    }
    _assert_no_sensitive_details(
        mobile_metadata,
        [
            private_text,
            r"C:\\Users\\Suli",
            "secretPREVIEW123456",
            "#password",
            "metadata.internal.local",
            "Metadata Host",
            device_id,
            grant_id,
        ],
    )


def test_remote_input_unverified_dry_run_does_not_create_mobile_approval(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    dry_run_secret = "secret" + "DRYRUN123456"
    preview = {
        "ok": False,
        "dry_run": False,
        "error": (
            r"preview failed at C:\\Users\\Suli\\Desktop\\secrets\\dry-run.txt "
            f"token={dry_run_secret} selector=#dry-run hostname=dry-run.internal.local"
        ),
        "diff_preview": [{"action": "click", "x": 100, "y": 200, "selector": "#dry-run"}],
    }
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    token, grant_id = _remote_input_grant_token("mobile_input_unverified_preview", "Preview Host")
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
        "status_code": 409,
    }
    assert db.fetch_many("approvals", limit=20) == []
    _assert_no_sensitive_details(
        error,
        [
            r"C:\\Users\\Suli",
            dry_run_secret,
            "#dry-run",
            "dry-run.internal.local",
            "mobile_input_unverified_preview",
            grant_id,
            "Preview Host",
        ],
    )


def test_remote_input_tool_metadata_missing_falls_back_to_safe_mobile_boundary(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    tool = registry.get("remote.click")
    tool.effects = []
    tool.resource_kinds = []
    tool.trust_tier = ""
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(
        tool,
        "execute",
        lambda args, context: {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 5, "y": 6}]},
    )
    _seed_remote_frame(device_id="mobile_metadata_fallback", grant_id="rig_metadata_fallback")

    result = routes_remote.handle_remote_input_event(
        {"type": "click", "x": 5, "y": 6},
        claims={"device_id": "mobile_metadata_fallback", "grant_id": "rig_metadata_fallback"},
    )

    approval = db.fetch_one("approvals", result["approval_id"])
    assert approval is not None
    mobile_payload = mobile_pairing_service.safe_approval_payload(approval)
    assert mobile_payload["tool_trust_tier"] == "unknown"
    assert mobile_payload["tool_effects"] == ["click", "write"]
    assert mobile_payload["resource_kinds"] == ["remote_screen", "desktop_ui"]
    assert mobile_payload["engineering_boundary"]["tool"]["trust_tier"] == "unknown"
    assert mobile_payload["engineering_boundary"]["tool"]["effects"] == ["click", "write"]
    assert mobile_payload["engineering_boundary"]["tool"]["resource_kinds"] == ["remote_screen", "desktop_ui"]


def test_remote_input_tool_without_dry_run_metadata_fails_closed(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    tool = registry.get("remote.click")
    tool.supports_dry_run = False
    calls: list[dict[str, object]] = []

    def fake_execute(args: dict[str, object], context: dict[str, object]) -> dict[str, object]:  # noqa: ARG001
        calls.append(dict(args))
        return {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 5, "y": 6}]}

    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(tool, "execute", fake_execute)
    _seed_remote_frame(device_id="mobile_no_dry_run", grant_id="rig_no_dry_run")

    with pytest.raises(HTTPException) as exc_info:
        routes_remote.handle_remote_input_event(
            {"type": "click", "x": 5, "y": 6},
            claims={"device_id": "mobile_no_dry_run", "grant_id": "rig_no_dry_run"},
        )

    assert exc_info.value.status_code == 409
    assert calls == []
    assert db.fetch_many("approvals", limit=20) == []


def test_remote_input_policy_deny_uses_generic_client_and_summarized_audit():
    _enable_remote_desktop()
    deny_secret = "secret" + "DENYREASON123456"
    raw_reason = (
        r"deny remote input at C:\\Users\\Suli\\Desktop\\secrets\\deny.txt "
        f"token={deny_secret} selector=#deny-secret "
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
        r"C:\\Users\\Suli",
        deny_secret,
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
        event for event in db.fetch_many("audit_events", limit=20) if event["event_type"] == "remote.input.rejected"
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
    policy_secret = "secret" + "POLICY123456"
    raw_error = (
        rf"policy failed at C:\\Users\\Suli\\Desktop\\secrets\\policy.txt "
        rf"token={policy_secret} selector=#policy-secret hostname=policy.internal.local "
        rf"device_id={device_id} grant_id={grant_id} "
        "Traceback (most recent call last): File "
        r'"C:\\Users\\Suli\\Desktop\\lengrvis\\backend\\app\\policy\\policy_engine.py", line 201, in review'
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
        r"C:\\Users\\Suli",
        policy_secret,
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
        fragment for fragment in client_sensitive_fragments if fragment not in {"policy failed", "HTTPException"}
    ]
    _assert_no_sensitive_details(error, client_sensitive_fragments)
    failure = next(
        event for event in db.fetch_many("audit_events", limit=20) if event["event_type"] == "remote.input.rejected"
    )
    _assert_no_sensitive_details(failure["payload"], audit_sensitive_fragments)
    assert "[REDACTED" in json.dumps(failure["payload"], ensure_ascii=False)


def test_remote_input_unexpected_exception_sends_generic_error(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    raw_error = (
        r"input crashed at C:\\Users\\Suli\\Desktop\\secrets\\input.txt "
        "token=secretINPUT123456 selector=#api-key "
        "hostname=input-host.internal.local "
        "Traceback (most recent call last): File "
        r'"C:\\Users\\Suli\\Desktop\\lengrvis\\backend\\app\\api\\routes_remote.py", line 201, in input'
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
            r"C:\\Users\\Suli",
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
        event for event in db.fetch_many("audit_events", limit=20) if event["event_type"] == "remote.input.failed"
    )
    _assert_no_sensitive_details(
        failure["payload"],
        [
            r"C:\\Users\\Suli",
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


def test_remote_exception_audit_redacts_short_token_hints(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    raw_error = "input failed with token=abc123 and Bearer abcd selector=#short-token"

    def fail_input(event, *, claims=None):
        raise RuntimeError(raw_error)

    monkeypatch.setattr(routes_remote, "handle_remote_input_event", fail_input)
    mobile_pairing_service._upsert_mobile_device(device_id="mobile_short_token", device_name="Short Token Phone")
    grant = mobile_pairing_service.create_remote_input_grant("mobile_short_token")
    claimed = mobile_pairing_service.claim_remote_input_grant_token(
        grant["grant_id"],
        {"device_id": "mobile_short_token", "device_name": "Short Token Phone"},
    )
    client = TestClient(_test_app())

    with client.websocket_connect(
        "/ws/remote/input",
        subprotocols=[f"{MOBILE_AUTH_WS_PROTOCOL_PREFIX}{claimed['token']}"],
    ) as websocket:
        assert websocket.receive_json()["type"] == "connected"
        websocket.send_json({"type": "click", "x": 100, "y": 200})
        error = websocket.receive_json()

    assert error["message"] == "Remote input event could not be handled."
    failure = next(
        event for event in db.fetch_many("audit_events", limit=20) if event["event_type"] == "remote.input.failed"
    )
    error_text = json.dumps(failure["payload"], ensure_ascii=False)
    assert "token=abc123" not in error_text
    assert "Bearer abcd" not in error_text
    assert "#short-token" not in error_text
    assert "[REDACTED_SECRET]" in error_text


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

    assert exc_info.value.code == REMOTE_WS_GRANT_CLOSE_CODE


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

    assert exc_info.value.code == REMOTE_WS_GRANT_CLOSE_CODE


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

    assert exc_info.value.code == REMOTE_WS_GRANT_CLOSE_CODE


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

    assert exc_info.value.code == REMOTE_WS_AUTH_CLOSE_CODE


def test_idle_remote_input_closes_after_token_expires(monkeypatch: pytest.MonkeyPatch):
    _enable_remote_desktop()
    preview = {"ok": True, "dry_run": True, "diff_preview": [{"action": "click", "x": 100, "y": 200}]}
    registry = register_all_tools(settings=get_effective_settings(), load_skills=False)
    monkeypatch.setattr(routes_remote, "register_all_tools", lambda settings=None: registry)
    monkeypatch.setattr(registry.get("remote.click"), "execute", lambda args, context: preview)
    mobile_pairing_service._upsert_mobile_device(
        device_id="mobile_input_idle_token_expiring", device_name="Input Phone"
    )
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

    assert exc_info.value.code == REMOTE_WS_AUTH_CLOSE_CODE


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
        _disable_remote_desktop()
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == REMOTE_WS_RETRY_CLOSE_CODE
