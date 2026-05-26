from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.core import db
from app.services import settings_service


def test_settings_rejects_remote_url_for_local_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    response = client.post(
        "/api/settings",
        json={"provider_name": "ollama", "base_url": "https://example.com/localhost/v1"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_local_llm_base_url"


def test_settings_rejects_persisted_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
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


def test_llm_profile_and_cost_summary_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    profile = client.get("/api/settings/llm/profile")
    summary = client.get("/api/settings/llm/cost-summary")

    assert profile.status_code == 200
    assert "profile" in profile.json()
    assert summary.status_code == 200
    assert summary.json()["calls"] == 0


def test_llm_health_includes_active_provider_and_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    response = client.get("/api/settings/llm/health")

    assert response.status_code == 200
    body = response.json()
    assert "active" in body
    assert "retry" in body
    assert "circuit" in body["retry"]


def test_llm_profile_redacts_provider_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))

    def fail_provider(settings):
        raise RuntimeError("provider failed token=supersecrettokenvalue1234567890")

    monkeypatch.setattr(settings_service, "get_provider_for_mode", fail_provider)
    client = TestClient(create_app())

    response = client.get("/api/settings/llm/profile")

    assert response.status_code == 200
    error = response.json()["error"]
    assert "supersecrettokenvalue" not in error
    assert "[REDACTED" in error


def test_llm_health_redacts_provider_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))

    def fail_provider(settings):
        raise RuntimeError("provider failed https://api.example.test/v1?api_key=secretapikeyvalue123456")

    monkeypatch.setattr(settings_service, "get_provider_for_mode", fail_provider)
    client = TestClient(create_app())

    response = client.get("/api/settings/llm/health")

    assert response.status_code == 200
    error = response.json()["active"]["error"]
    assert "secretapikeyvalue" not in error
    assert "api_key=%5BREDACTED%5D" in error


def test_llm_provider_test_redacts_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))

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
