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
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("LENGRVIS_LLM_API_MAX_RETRIES", raising=False)
    monkeypatch.delenv("LENGRVIS_RECOVERY_MAX_RETRIES", raising=False)

    settings = AppSettings.from_sources()

    assert settings.llm_api_max_retries == 0
    assert settings.llm_api_retry_backoff_seconds == 0.0
    assert settings.llm_api_circuit_cooldown_seconds == 0.0
    assert settings.recovery_max_retries == 0


def test_os_reflection_limits_from_yaml_and_env(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    data_dir = tmp_path / "data"
    config_path.write_text(
        "\n".join(
            [
                "orchestration:",
                "  os_reflection_max_per_run: 5",
                "  os_reflection_max_per_step: 3",
                "paths:",
                f"  data_dir: {data_dir.as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(config_path))
    monkeypatch.delenv("LENGRVIS_OS_REFLECTION_MAX_PER_RUN", raising=False)
    monkeypatch.delenv("LENGRVIS_OS_REFLECTION_MAX_PER_STEP", raising=False)

    settings = AppSettings.from_sources()
    assert settings.os_reflection_max_per_run == 5
    assert settings.os_reflection_max_per_step == 3

    monkeypatch.setenv("LENGRVIS_OS_REFLECTION_MAX_PER_RUN", "0")
    overridden = AppSettings.from_sources()
    assert overridden.os_reflection_max_per_run == 0
    assert overridden.os_reflection_max_per_step == 3


def test_env_values_override_yaml(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("llm:\n  llm_api_max_retries: 0\n", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("LENGRVIS_LLM_API_MAX_RETRIES", "4")

    settings = AppSettings.from_sources()

    assert settings.llm_api_max_retries == 4


def test_default_jwt_secret_is_reused_across_config_reloads(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.delenv("LENGRVIS_JWT_SECRET", raising=False)

    first = AppSettings.from_sources()
    second = AppSettings.from_sources()

    from app.security.local_secret import read_local_secret

    assert first.jwt_secret == second.jwt_secret
    assert first.jwt_secret
    assert read_local_secret(data_dir / "mobile_jwt.secret") == first.jwt_secret


def test_env_jwt_secret_overrides_local_secret(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "mobile_jwt.secret").write_text("local-secret", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("LENGRVIS_JWT_SECRET", "env-secret")

    settings = AppSettings.from_sources()

    assert settings.jwt_secret == "env-secret"


def test_invalid_numeric_settings_fall_back_without_crashing(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "llm:",
                "  temperature: nope",
                "  max_tokens: nope",
                "  timeout: nope",
                "privacy:",
                "  browser_max_page_bytes: nope",
                "  document_max_chars_to_llm: nope",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("LENGRVIS_TEMPERATURE", raising=False)
    monkeypatch.delenv("LENGRVIS_MAX_TOKENS", raising=False)
    monkeypatch.delenv("LENGRVIS_TIMEOUT", raising=False)
    monkeypatch.delenv("LENGRVIS_BROWSER_MAX_PAGE_BYTES", raising=False)
    monkeypatch.delenv("LENGRVIS_DOCUMENT_MAX_CHARS_TO_LLM", raising=False)

    settings = AppSettings.from_sources()

    assert settings.temperature == 0.2
    assert settings.max_tokens == 1600
    assert settings.timeout == 30
    assert settings.browser_max_page_bytes == 250000
    assert settings.document_max_chars_to_llm == 30000


def test_perception_yaml_keys_match_example_config(monkeypatch, tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "perception:",
                "  local_ocr_enabled: true",
                "  frame_diff_threshold: 0.25",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("LENGRVIS_PERCEPTION_LOCAL_OCR_ENABLED", raising=False)
    monkeypatch.delenv("LENGRVIS_PERCEPTION_FRAME_DIFF_THRESHOLD", raising=False)

    settings = AppSettings.from_sources()

    assert settings.perception_local_ocr_enabled is True
    assert settings.perception_frame_diff_threshold == 0.25


def test_encrypted_api_key_is_decrypted_when_plain_key_missing(monkeypatch, tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "LENGRVIS_PROVIDER_NAME=openai_compatible",
                "LENGRVIS_BASE_URL=https://api.example.test",
                "LENGRVIS_API_KEY_ENCRYPTED=dpapi:encrypted-test-key",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(env_path))
    monkeypatch.delenv("LENGRVIS_API_KEY", raising=False)
    monkeypatch.delenv("LENGRVIS_API_KEY_ENCRYPTED", raising=False)
    monkeypatch.setattr("app.config._decrypt_windows_dpapi", lambda value: f"decrypted:{value}")

    settings = AppSettings.from_sources()

    assert settings.api_key == "decrypted:dpapi:encrypted-test-key"


def test_settings_endpoint_coerces_resilience_fields(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
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


def test_settings_endpoint_rejects_invalid_numeric_fields(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    client = TestClient(create_app())

    response = client.post("/api/settings", json={"max_tokens": "nope"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_numeric_setting"


def test_settings_endpoint_requires_confirmation_for_permission_mode_relaxation(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    client = TestClient(create_app())

    rejected = client.post("/api/settings", json={"permission_mode": "dontask"})

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "sensitive_confirmation_required"

    confirmation = client.post("/api/settings/confirm-sensitive-change", json={"permission_mode": "dontask"})
    assert confirmation.status_code == 200
    confirmation_body = confirmation.json()
    assert confirmation_body["required"] is True
    assert confirmation_body["changes"][0]["kind"] == "settings_permission_mode_relaxation"
    assert confirmation_body["changes"][0]["to"] == "dont_ask"

    accepted = client.post(
        "/api/settings",
        json={"permission_mode": "dontask", "confirmation_nonce": confirmation_body["nonce"]},
    )

    assert accepted.status_code == 200
    assert accepted.json()["permission_mode"] == "dont_ask"


def test_settings_endpoint_rejects_secret_settings(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.init_db()
    client = TestClient(create_app())

    response = client.post("/api/settings", json={"api_key": "sk-test-secret"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "secret_settings_must_use_external_config"
