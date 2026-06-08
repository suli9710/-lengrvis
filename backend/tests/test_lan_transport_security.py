from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import AppSettings
from app.core import db
from app.main import create_app
from tls_test_material import write_lan_tls_material


def test_app_settings_loads_lan_tls_from_yaml_and_env(monkeypatch, tmp_path: Path):
    cert_file = tmp_path / "lan.crt"
    key_file = tmp_path / "lan.key"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "transport:",
                "  lan_tls_enabled: true",
                f"  lan_tls_cert_file: {cert_file.as_posix()}",
                f"  lan_tls_key_file: {key_file.as_posix()}",
                "  lan_public_base_url: https://yaml-lan.example.test:9443",
            ]
        ),
        encoding="utf-8",
    )
    _isolate_config(monkeypatch, tmp_path, config_path=config_path)

    settings = AppSettings.from_sources()

    assert settings.lan_tls_enabled is True
    assert Path(settings.lan_tls_cert_file).resolve() == cert_file.resolve()
    assert Path(settings.lan_tls_key_file).resolve() == key_file.resolve()
    assert settings.lan_public_base_url == "https://yaml-lan.example.test:9443"

    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "false")
    monkeypatch.setenv("LENGRVIS_LAN_PUBLIC_BASE_URL", "https://env-lan.example.test:9443")

    overridden = AppSettings.from_sources()

    assert overridden.lan_tls_enabled is False
    assert overridden.lan_public_base_url == "https://env-lan.example.test:9443"


def test_system_diagnostics_reports_default_http_lan_readiness(monkeypatch, tmp_path: Path):
    _isolate_config(monkeypatch, tmp_path)
    db.init_db()

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    transport = response.json()["lan_transport"]
    assert transport["status"] == "http_lan_insecure"
    assert transport["scheme"] == "http"
    assert transport["https_enabled"] is False
    assert transport["tls_ready"] is False
    assert transport["trust_required"] is False
    assert transport["certificate_trust"] == "not_required"
    assert transport["origin"].startswith("http://")


def test_system_diagnostics_reports_tls_enabled_with_missing_files(monkeypatch, tmp_path: Path):
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(tmp_path / "missing.crt"))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(tmp_path / "missing.key"))
    monkeypatch.setenv("LENGRVIS_LAN_PUBLIC_BASE_URL", "https://phone-lan.example.test:8443")
    db.init_db()

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    transport = response.json()["lan_transport"]
    assert transport["status"] == "https_misconfigured"
    assert transport["scheme"] == "https"
    assert transport["origin"] == "https://phone-lan.example.test:8443"
    assert transport["https_enabled"] is True
    assert transport["tls_ready"] is False
    assert transport["cert_configured"] is True
    assert transport["key_configured"] is True
    assert transport["cert_present"] is False
    assert transport["key_present"] is False
    assert transport["trust_required"] is True
    assert transport["certificate_trust"] == "requires_client_trust_unknown_issuer"
    assert "missing" in transport["warning"]


def test_pairing_responses_include_tls_transport_metadata_when_files_exist(monkeypatch, tmp_path: Path):
    _isolate_config(monkeypatch, tmp_path)
    cert_file, key_file = write_lan_tls_material(tmp_path)
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(cert_file))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(key_file))
    monkeypatch.setenv("LENGRVIS_LAN_PUBLIC_BASE_URL", "https://phone-lan.example.test:8443")
    db.init_db()
    client = TestClient(create_app())

    request_payload = client.post("/api/pair/request").json()
    confirm_response = client.post(
        "/api/pair/confirm",
        json={"code": request_payload["code"], "device_name": "Pixel"},
    )

    assert confirm_response.status_code == 200
    for payload in (request_payload, confirm_response.json()):
        transport = payload["transport_security"]
        assert payload["server"]["transport_security"] == transport
        assert payload["server_origin"] == "https://phone-lan.example.test:8443"
        assert payload["https_enabled"] is True
        assert payload["trust_required"] is True
        assert transport["status"] == "https_ready"
        assert transport["tls_ready"] is True
        assert transport["cert_present"] is True
        assert transport["key_present"] is True
        assert transport["tls_material_valid"] is True


def _isolate_config(monkeypatch, tmp_path: Path, *, config_path: Path | None = None) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(config_path or tmp_path / "missing.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.delenv("LENGRVIS_LAN_TLS_ENABLED", raising=False)
    monkeypatch.delenv("LENGRVIS_LAN_TLS_CERT_FILE", raising=False)
    monkeypatch.delenv("LENGRVIS_LAN_TLS_KEY_FILE", raising=False)
    monkeypatch.delenv("LENGRVIS_LAN_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("LENGRVIS_BACKEND_PORT", raising=False)
