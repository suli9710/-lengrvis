from __future__ import annotations

from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record
from app.core.schemas import Task, ToolResult
from app.main import create_app
from app.orchestration.task_phase import TaskPhase


def test_system_diagnostics_include_local_product_metrics(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()
    record("diagnostics.ready", "pytest", {"ok": True})
    failed_task = Task(user_goal="diagnostic failure sample", status=TaskPhase.FAILED, phase=TaskPhase.FAILED)
    db.upsert_model("tasks", failed_task)
    db.upsert_model("tool_results", ToolResult(tool_call_id="tool_missing", ok=False, error="failed"))

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["product"]["name"] == "Lengrvis"
    assert payload["product"]["version"] == "0.1.0"
    assert payload["local_paths"]["data_dir"] == str(tmp_path)
    assert payload["local_paths"]["database"].endswith("lengrvis.db")
    assert payload["local_paths"]["log_dirs"]
    assert payload["audit"]["verification"]["ok"] is True
    assert payload["audit"]["verification"]["checked"] >= 1
    assert payload["recent_failure_counts"]["tasks_failed"] == 1
    assert payload["recent_failure_counts"]["tool_results_failed"] == 1
    assert payload["lan_transport"]["status"] == "http_lan_insecure"
    assert payload["lan_transport"]["tls_ready"] is False


def test_system_diagnostics_include_lan_tls_readiness(monkeypatch, tmp_path):
    cert = tmp_path / "lan.crt"
    key = tmp_path / "lan.key"
    cert.write_text("fake cert for readiness metadata", encoding="utf-8")
    key.write_text("fake key for readiness metadata", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_ENABLED", "true")
    monkeypatch.setenv("LENGRVIS_LAN_TLS_CERT_FILE", str(cert))
    monkeypatch.setenv("LENGRVIS_LAN_TLS_KEY_FILE", str(key))
    monkeypatch.setenv("LENGRVIS_LAN_PUBLIC_BASE_URL", "https://lengrvis.local:8443")
    db.init_db()

    response = TestClient(create_app()).get("/api/system/diagnostics")

    assert response.status_code == 200
    transport = response.json()["lan_transport"]
    assert transport["status"] == "https_ready"
    assert transport["origin"] == "https://lengrvis.local:8443"
    assert transport["tls_ready"] is True
    assert transport["requires_trust"] is True
