from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from app.api import routes_remote
from app.core import db
from app.llm.registry import get_effective_settings
from app.policy.risk import RiskLevel
from app.security.mobile_jwt import issue_mobile_token
from app.services import remote_desktop_service
from app.services.settings_service import update_settings
from app.tools.registry import register_all_tools


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MARVIS_PROVIDER_NAME", "mock")
    monkeypatch.setenv("MARVIS_MODE", "efficiency")
    db.init_db()
    yield


def _test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_remote.ws_router)
    app.include_router(routes_remote.ws_router, prefix="/api")
    return app


def _mobile_token() -> str:
    return issue_mobile_token(device_id="mobile_test", device_name="Test Phone")


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

    update_settings({"remote_desktop_enabled": True})
    enabled = {"settings": get_effective_settings(), "allowed_directories": []}
    result = registry.get("remote.click").execute({"x": 1, "y": 2, "dry_run": False}, enabled)
    assert result["ok"] is False
    assert "approval_id" in result["error"]


def test_remote_disabled_by_default():
    client = TestClient(_test_app())

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/remote/screen?token={_mobile_token()}"):
            raise AssertionError("Remote desktop WebSocket should be disabled by default")

    assert exc_info.value.code == 1008


def test_input_events_audited(monkeypatch: pytest.MonkeyPatch):
    update_settings({"remote_desktop_enabled": True})
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
