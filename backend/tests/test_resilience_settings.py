from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.core import db
from app.main import create_app


def test_yaml_zero_values_are_preserved(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    data_dir = tmp_path / "data"
    config_path.write_text(
        "\n".join(
            [
                "llm:",
                "  llm_api_max_retries: 0",
                "  llm_api_retry_backoff_seconds: 0.0",
                "  llm_api_circuit_cooldown_seconds: 0.0",
                "orchestration:",
                "  recovery_max_retries: 0",
                "paths:",
                f"  data_dir: {data_dir.as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MARVIS_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("MARVIS_LLM_API_MAX_RETRIES", raising=False)
    monkeypatch.delenv("MARVIS_RECOVERY_MAX_RETRIES", raising=False)

    settings = AppSettings.from_sources()

    assert settings.llm_api_max_retries == 0
    assert settings.llm_api_retry_backoff_seconds == 0.0
    assert settings.llm_api_circuit_cooldown_seconds == 0.0
    assert settings.recovery_max_retries == 0


def test_env_values_override_yaml(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  llm_api_max_retries: 0\n", encoding="utf-8")
    monkeypatch.setenv("MARVIS_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("MARVIS_LLM_API_MAX_RETRIES", "4")

    settings = AppSettings.from_sources()

    assert settings.llm_api_max_retries == 4


def test_settings_endpoint_coerces_resilience_fields(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MARVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    client = TestClient(create_app())

    response = client.post(
        "/api/settings",
        json={
            "llm_api_max_retries": "-1",
            "recovery_max_retries": "-2",
            "llm_api_circuit_failure_threshold": "0",
            "llm_api_retry_backoff_seconds": "-0.5",
            "llm_api_circuit_cooldown_seconds": "2.5",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["llm_api_max_retries"] == 0
    assert payload["recovery_max_retries"] == 0
    assert payload["llm_api_circuit_failure_threshold"] == 1
    assert payload["llm_api_retry_backoff_seconds"] == 0.0
    assert payload["llm_api_circuit_cooldown_seconds"] == 2.5
