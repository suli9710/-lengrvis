"""Regression tests for desktop HTTP API token guard (P1-21)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.main import create_app
from app.security.desktop_api import DESKTOP_API_TOKEN_HEADER

pytestmark = pytest.mark.requires_desktop_api_token


@pytest.fixture(autouse=True)
def _require_desktop_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_DESKTOP_API_TOKEN", "desktop-http-secret")
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    db.init_db()


def test_state_changing_http_requests_without_token_return_401() -> None:
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    assert client.get("/api/tasks").status_code == 401
    assert client.post("/api/chat", json={"message": "hi", "mode": "efficiency"}).status_code == 401


def test_state_changing_http_requests_with_token_are_allowed() -> None:
    client = TestClient(create_app(), client=("127.0.0.1", 50100))
    headers = {DESKTOP_API_TOKEN_HEADER: "desktop-http-secret"}

    assert client.get("/api/tasks", headers=headers).status_code == 200


def test_health_endpoint_does_not_require_desktop_token() -> None:
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    assert client.get("/api/health").status_code == 200
