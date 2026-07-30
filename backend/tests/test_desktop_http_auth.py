"""Regression tests for desktop HTTP API token guard (P1-21)."""

from __future__ import annotations

import hmac
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from app.core import db
from app.main import create_app
from app.security.desktop_api import DESKTOP_API_TOKEN_HEADER

pytestmark = pytest.mark.desktop_api_auth_contract


@pytest.fixture(autouse=True)
def _require_desktop_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LENGRVIS_DESKTOP_API_TOKEN_OPTIONAL", raising=False)
    db.init_db()


@pytest.mark.parametrize(
    ("method", "path", "json_body"),
    [
        ("post", "/api/chat", {"message": "hi", "mode": "efficiency"}),
        ("post", "/api/pair/code", None),
        ("post", "/api/system/diagnostics/export", {}),
    ],
    ids=["chat", "pair-code", "diagnostics-export"],
)
@pytest.mark.parametrize("supplied_token", [None, "wrong-token"], ids=["missing-token", "wrong-token"])
def test_state_changing_http_requests_require_valid_token(
    method: str,
    path: str,
    json_body: dict[str, str] | None,
    supplied_token: str | None,
) -> None:
    client = TestClient(create_app(), client=("127.0.0.1", 50100))
    headers = {DESKTOP_API_TOKEN_HEADER: supplied_token} if supplied_token is not None else None

    response = client.request(method, path, json=json_body, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Missing desktop API token"


def test_state_changing_http_requests_with_token_are_allowed(desktop_api_headers: dict[str, str]) -> None:
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    assert client.get("/api/tasks", headers=desktop_api_headers).status_code == 200


def test_invalid_desktop_token_is_rejected() -> None:
    # Guards has_valid_desktop_api_token / hmac.compare_digest: a wrong token
    # must never be accepted.
    client = TestClient(create_app(), client=("127.0.0.1", 50100))

    assert client.get("/api/tasks", headers={DESKTOP_API_TOKEN_HEADER: "wrong-token"}).status_code == 401


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
            b"pytest-desktop-api-token",
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
