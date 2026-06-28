from __future__ import annotations

import logging

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.core import db
from app.main import create_app
from app.services import settings_service


def test_settings_rejects_remote_url_for_local_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    response = client.post(
        "/api/settings",
        json={"provider_name": "ollama", "base_url": "https://example.com/localhost/v1"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_local_llm_base_url"


def test_settings_rejects_persisted_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    response = client.post(
        "/api/settings",
        json={"api_key": "sk-secret-value-that-must-not-persist", "jwt_secret": "jwt-secret-value"},
    )

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "secret_settings_must_use_external_config"
    assert "sk-secret-value" not in body["message"]
    assert "jwt-secret-value" not in body["message"]
    overrides = db.get_settings_overrides()
    assert "api_key" not in overrides
    assert "jwt_secret" not in overrides


def test_settings_public_dict_redacts_mcp_auth():
    from app.config import AppSettings

    settings = AppSettings(
        mcp_servers=[
            {
                "name": "private-mcp",
                "url": "https://mcp.example",
                "auth": {"token": "secret-token", "authorization": "Bearer secret", "nested": {"password": "pw"}},
            }
        ]
    )

    public = settings.public_dict()
    rendered = str(public)

    assert "secret-token" not in rendered
    assert "Bearer secret" not in rendered
    assert "'pw'" not in rendered
    assert public["mcp_servers"][0]["auth"] == "***"


def test_llm_profile_and_cost_summary_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    profile = client.get("/api/settings/llm/profile")
    summary = client.get("/api/settings/llm/cost-summary")

    assert profile.status_code == 200
    assert "profile" in profile.json()
    assert summary.status_code == 200
    assert summary.json()["calls"] == 0


def test_llm_health_includes_active_provider_and_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/settings/llm/health")

    assert response.status_code == 200
    body = response.json()
    assert "active" in body
    assert "retry" in body
    assert "circuit" in body["retry"]


def test_llm_profile_redacts_provider_errors(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    caplog.set_level(logging.WARNING, logger="app.services.settings_service")

    def fail_provider(settings):
        raise RuntimeError("provider failed token=supersecrettokenvalue1234567890")

    monkeypatch.setattr(settings_service, "get_provider_for_mode", fail_provider)
    client = TestClient(create_app())

    response = client.get("/api/settings/llm/profile")

    assert response.status_code == 200
    error = response.json()["error"]
    assert "supersecrettokenvalue" not in error
    assert "[REDACTED" in error
    assert "settings.llm_profile" in caplog.text
    assert "supersecrettokenvalue" not in caplog.text


def test_llm_health_redacts_provider_errors(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    caplog.set_level(logging.WARNING, logger="app.services.settings_service")

    def fail_provider(settings):
        raise RuntimeError("provider failed https://api.example.test/v1?api_key=secretapikeyvalue123456")

    monkeypatch.setattr(settings_service, "get_provider_for_mode", fail_provider)
    client = TestClient(create_app())

    response = client.get("/api/settings/llm/health")

    assert response.status_code == 200
    error = response.json()["active"]["error"]
    assert "secretapikeyvalue" not in error
    assert "api_key=%5BREDACTED%5D" in error
    assert "settings.llm_health" in caplog.text
    assert "secretapikeyvalue" not in caplog.text


def test_llm_provider_test_redacts_errors(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    caplog.set_level(logging.WARNING, logger="app.services.settings_service")

    class FailingProvider:
        name = "custom_http"

        async def chat(self, messages):
            raise RuntimeError("upstream Authorization: Bearer secretbearertokenvalue1234567890")

    monkeypatch.setattr(settings_service, "get_provider", lambda: FailingProvider())
    client = TestClient(create_app())

    response = client.post("/api/settings/test-llm-provider")

    assert response.status_code == 200
    error = response.json()["error"]
    assert "secretbearertokenvalue" not in error
    assert "Bearer [REDACTED]" in error
    assert "settings.test_llm_provider" in caplog.text
    assert "secretbearertokenvalue" not in caplog.text


def test_sensitive_settings_require_bound_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    blocked = client.post("/api/settings", json={"remote_desktop_enabled": True})
    browser_network_blocked = client.post("/api/settings", json={"allow_browser_network": True})
    confirmation = client.post(
        "/api/settings/confirm-sensitive-change",
        json={"remote_desktop_enabled": True},
    )
    nonce = confirmation.json()["nonce"]
    tampered = client.post(
        "/api/settings",
        json={"remote_desktop_enabled": True, "allow_cloud_context": True, "confirmation_nonce": nonce},
    )
    allowed = client.post(
        "/api/settings",
        json={"remote_desktop_enabled": True, "confirmation_nonce": nonce},
    )
    reused_for_other_risk = client.post(
        "/api/settings",
        json={"allow_cloud_context": True, "confirmation_nonce": nonce},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert browser_network_blocked.status_code == 409
    assert browser_network_blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert confirmation.status_code == 200
    assert confirmation.json()["required"] is True
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "sensitive_confirmation_invalid"
    assert allowed.status_code == 200
    assert allowed.json()["remote_desktop_enabled"] is True
    assert reused_for_other_risk.status_code == 409
    assert reused_for_other_risk.json()["error"]["code"] == "sensitive_confirmation_invalid"


def test_sensitive_settings_confirmation_required_when_disabling_native_sensitive_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    db.init_db()
    db.set_setting("remote_desktop_enabled", True)
    client = TestClient(create_app())

    blocked = client.post("/api/settings", json={"remote_desktop_enabled": False})
    confirmation = client.post(
        "/api/settings/confirm-sensitive-change",
        json={"remote_desktop_enabled": False},
    )
    allowed = client.post(
        "/api/settings",
        json={"remote_desktop_enabled": False, "confirmation_nonce": confirmation.json()["nonce"]},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert confirmation.status_code == 200
    assert confirmation.json()["required"] is True
    assert allowed.status_code == 200
    assert allowed.json()["remote_desktop_enabled"] is False


def test_sensitive_settings_confirmation_nonce_allows_same_or_narrower_patch(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())
    patch = {"remote_desktop_enabled": True, "allow_cloud_context": True}

    confirmation = client.post("/api/settings/confirm-sensitive-change", json=patch)
    nonce = confirmation.json()["nonce"]
    tampered = client.post(
        "/api/settings",
        json={"remote_desktop_enabled": False, "confirmation_nonce": nonce},
    )
    allowed = client.post(
        "/api/settings",
        json={"remote_desktop_enabled": True, "confirmation_nonce": nonce},
    )

    assert confirmation.status_code == 200
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "sensitive_confirmation_invalid"
    assert allowed.status_code == 200
    assert allowed.json()["remote_desktop_enabled"] is True
    assert allowed.json()["allow_cloud_context"] is False


def test_sensitive_settings_require_confirmation_for_auth_scope_and_mcp_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())
    docs = str(tmp_path / "docs")
    mcp_server = {"name": "local-tools", "url": "http://127.0.0.1:8765/mcp", "transport": "http", "enabled": True}

    auth_blocked = client.post("/api/settings", json={"requires_openai_auth": False})
    directory_blocked = client.post("/api/settings", json={"allowed_directories": [docs]})
    mcp_blocked = client.post("/api/settings", json={"mcp_servers": [mcp_server]})
    confirmation = client.post(
        "/api/settings/confirm-sensitive-change",
        json={
            "requires_openai_auth": False,
            "allowed_directories": [docs],
            "mcp_servers": [mcp_server],
        },
    )
    allowed = client.post(
        "/api/settings",
        json={
            "requires_openai_auth": False,
            "allowed_directories": [docs],
            "mcp_servers": [mcp_server],
            "confirmation_nonce": confirmation.json()["nonce"],
        },
    )

    assert auth_blocked.status_code == 409
    assert directory_blocked.status_code == 409
    assert mcp_blocked.status_code == 409
    assert confirmation.status_code == 200
    assert {change["kind"] for change in confirmation.json()["changes"]} == {
        "settings_disable_auth",
        "settings_expand_allowed_directories",
        "settings_enable_mcp_servers",
    }
    assert allowed.status_code == 200
    assert allowed.json()["requires_openai_auth"] is False
    assert allowed.json()["allowed_directories"] == [docs]
    assert allowed.json()["mcp_servers"][0]["url"] == mcp_server["url"]


def test_sensitive_settings_require_confirmation_for_llm_egress_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())
    payload = {
        "provider_name": "custom_http",
        "base_url": "https://attacker.example/v1",
        "wire_api": "responses",
    }

    blocked = client.post("/api/settings", json=payload)
    confirmation = client.post("/api/settings/confirm-sensitive-change", json=payload)
    tampered = client.post(
        "/api/settings",
        json={**payload, "base_url": "https://other.example/v1", "confirmation_nonce": confirmation.json()["nonce"]},
    )
    allowed = client.post("/api/settings", json={**payload, "confirmation_nonce": confirmation.json()["nonce"]})

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert confirmation.status_code == 200
    assert confirmation.json()["required"] is True
    changes_by_key = {change["key"]: change for change in confirmation.json()["changes"]}
    assert {"base_url", "provider_name", "wire_api"}.issubset(changes_by_key)
    assert changes_by_key["base_url"]["to"] == payload["base_url"]
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "sensitive_confirmation_invalid"
    assert allowed.status_code == 200
    assert allowed.json()["provider_name"] == payload["provider_name"]
    assert allowed.json()["base_url"] == payload["base_url"]
    assert allowed.json()["wire_api"] == payload["wire_api"]


def test_sensitive_settings_confirmation_nonce_is_one_shot(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    confirmation = client.post(
        "/api/settings/confirm-sensitive-change",
        json={"allow_browser_network": True},
    )
    nonce = confirmation.json()["nonce"]
    first = client.post("/api/settings", json={"allow_browser_network": True, "confirmation_nonce": nonce})
    db.set_setting("allow_browser_network", False)
    second = client.post("/api/settings", json={"allow_browser_network": True, "confirmation_nonce": nonce})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "sensitive_confirmation_invalid"


def test_add_directory_route_requires_sensitive_confirmation(tmp_path, monkeypatch):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())
    scope = str(tmp_path / "workspace")
    Path(scope).mkdir(parents=True, exist_ok=True)

    blocked = client.post("/api/index/add-directory", json={"path": scope})
    confirmation = client.post(
        "/api/settings/confirm-sensitive-change",
        json={"allowed_directories": [scope]},
    )
    allowed = client.post(
        "/api/index/add-directory",
        json={"path": scope, "confirmation_nonce": confirmation.json()["nonce"]},
    )

    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "sensitive_confirmation_required"
    assert allowed.status_code == 200
    assert allowed.json()["allowed_directories"] == [scope]


def test_public_settings_deep_redacts_mcp_server_secrets():
    settings = AppSettings(
        mcp_servers=[
            {
                "name": "tools",
                "url": "http://127.0.0.1:8765/mcp",
                "auth": {
                    "authorization": "Bearer secret-token",
                    "nested": {"password": "p@ss", "clientSecret": "secret"},
                },
                "headers": {"x-api-token": "token-value"},
            }
        ]
    )

    public = settings.public_dict()
    text = str(public)

    assert "secret-token" not in text
    assert "p@ss" not in text
    assert "token-value" not in text
    assert public["mcp_servers"][0]["auth"] == "***"
    assert public["mcp_servers"][0]["headers"]["x-api-token"] == "***"
