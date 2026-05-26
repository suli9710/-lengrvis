from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_settings_rejects_remote_url_for_local_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path))
    client = TestClient(create_app())

    response = client.post(
        "/api/settings",
        json={"provider_name": "ollama", "base_url": "https://example.com/localhost/v1"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsafe_local_llm_base_url"


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
