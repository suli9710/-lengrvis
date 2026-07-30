"""Regression tests for desktop HTTP API token guard (P1-21)."""

from __future__ import annotations

import hmac
from hashlib import sha256

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


def test_invalid_desktop_token_is_rejected() -> None:
    # Guards has_valid_desktop_api_token / hmac.compare_digest: a wrong token
    # must never be accepted. Runs guard-on (most of the suite runs guard-off
    # via the desktop_api_token_optional collection default), so a regression in
    # the comparison would otherwise go uncaught.
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    assert client.get("/api/tasks", headers={DESKTOP_API_TOKEN_HEADER: "wrong-token"}).status_code == 401


def test_non_exempt_state_changing_route_requires_desktop_token() -> None:
    # Guards _is_desktop_api_token_exempt_path against becoming over-broad: a
    # mutating /api/system/* route must not fall into the exempt set. The
    # middleware guard rejects before routing, so this holds regardless of the
    # handler.
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    assert client.post("/api/system/diagnostics/export", json={}).status_code == 401


def test_health_endpoint_does_not_require_desktop_token() -> None:
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    assert client.get("/api/health").status_code == 200


def test_loopback_health_challenge_proves_backend_identity_without_receiving_token() -> None:
    client = TestClient(create_app(), client=("127.0.0.1", 50100))
    challenge = "desktop-health-challenge-123456"

    response = client.get("/api/health", params={"desktop_challenge": challenge})

    assert response.status_code == 200
    assert (
        response.json()["desktop_proof"]
        == hmac.new(
            b"desktop-http-secret",
            challenge.encode("utf-8"),
            sha256,
        ).hexdigest()
    )


def test_health_challenge_rejects_unbounded_or_non_loopback_proof_requests() -> None:
    loopback = TestClient(create_app(), client=("127.0.0.1", 50100))
    remote = TestClient(create_app(), client=("192.168.1.50", 50100))

    assert (
        "desktop_proof"
        not in loopback.get(
            "/api/health",
            params={"desktop_challenge": "short"},
        ).json()
    )
    assert (
        "desktop_proof"
        not in loopback.get(
            "/api/health",
            params={"desktop_challenge": " desktop-health-challenge-123456 "},
        ).json()
    )
    assert (
        "desktop_proof"
        not in remote.get(
            "/api/health",
            params={"desktop_challenge": "desktop-health-challenge-123456"},
        ).json()
    )
