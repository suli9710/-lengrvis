from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_perception
from app.core import db
from app.security import middleware as security_middleware


def test_perception_capture_does_not_return_or_log_sensitive_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_error = (
        r"capture failed at C:\Users\John Doe\Secret Project\private.png "
        r"token=screen-secret-123456"
    )

    def fail_capture() -> dict:
        raise RuntimeError(raw_error)

    monkeypatch.setattr(routes_perception.perception_suggestion_service, "capture_once_summary", fail_capture)
    app = FastAPI()
    app.include_router(routes_perception.router, prefix="/api")

    with caplog.at_level(logging.WARNING, logger=routes_perception.logger.name), TestClient(app) as client:
        response = client.post("/api/perception/capture")

    assert response.status_code == 503
    assert response.json() == {"detail": "Perception capture is temporarily unavailable."}
    assert raw_error not in response.text
    assert r"C:\Users\John Doe" not in caplog.text
    assert "Secret Project" not in caplog.text
    assert "screen-secret-123456" not in caplog.text
    assert "[REDACTED_LOCAL_PATH]" in caplog.text
    assert "[REDACTED]" in caplog.text


def test_audit_fail_closed_response_does_not_expose_integrity_record_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_error = r"Sensitive record integrity failed for approvals:approval-secret at C:\Users\Private\audit.db"

    monkeypatch.setattr(security_middleware.db, "audit_fail_closed_enabled", lambda: True)

    def reject_write() -> None:
        raise db.SensitiveRecordIntegrityError(raw_error)

    monkeypatch.setattr(security_middleware.db, "require_audit_fail_closed_ok", reject_write)
    monkeypatch.setattr(security_middleware, "should_require_desktop_api_token", lambda _request: False)
    app = FastAPI()

    @app.post("/api/test-write")
    def test_write() -> dict[str, bool]:
        return {"ok": True}

    security_middleware.register_security_middleware(app)

    with TestClient(app) as client:
        response = client.post("/api/test-write")

    public_message = "Local audit integrity gate blocked this operation."
    assert response.status_code == 503
    assert response.json() == {
        "detail": public_message,
        "error": {"code": "audit_fail_closed", "message": public_message},
    }
    assert raw_error not in response.text
    assert "approval-secret" not in response.text
