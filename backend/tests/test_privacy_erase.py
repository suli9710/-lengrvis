"""PIPL/GDPR local data deletion entry (market-readiness checklist #14).

POST /api/system/privacy/erase-local-data must erase locally stored user
content and exported diagnostic packages, preserve the tamper-evident audit
chain (appending an erase event), and refuse to run without an explicit
confirmation phrase.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core import db
from app.core.audit import record, verify_chain
from app.core.schemas import Task, ToolResult
from app.main import create_app
from app.orchestration.task_phase import TaskPhase


def _setup_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    db.init_db()


def _seed_user_data(tmp_path) -> None:
    db.upsert_model(
        "tasks", Task(user_goal="private goal sample", status=TaskPhase.COMPLETED, phase=TaskPhase.COMPLETED)
    )
    db.upsert_model("tool_results", ToolResult(tool_call_id="tool_sample", ok=True, output={"note": "private"}))
    db.set_setting("preferred_mode", "privacy")
    db.upsert_memory({"id": "mem_sample", "content": "remember my private preference", "kind": "fact"})
    record("seed.event", "pytest", {"ok": True})
    export_dir = tmp_path / "diagnostic-packages"
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / "lengrvis-diagnostics-sample.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")


def test_erase_requires_explicit_confirmation(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    client = TestClient(create_app())

    for payload in ({}, {"confirm": ""}, {"confirm": "yes"}, {"confirm": "ERASE-LOCAL-DATA"}):
        response = client.post("/api/system/privacy/erase-local-data", json=payload)
        assert response.status_code == 400

    assert db.fetch_many("tasks", limit=10)
    assert (tmp_path / "diagnostic-packages" / "lengrvis-diagnostics-sample.json").exists()


def test_erase_deletes_user_content_and_packages_preserving_audit_chain(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    client = TestClient(create_app())

    response = client.post("/api/system/privacy/erase-local-data", json={"confirm": "erase-local-data"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["scope"] == "local_only"
    assert payload["deleted"]["rows_total"] >= 3
    assert payload["deleted"]["rows_by_table"]["tasks"] == 1
    assert payload["deleted"]["diagnostic_packages"] == 1
    assert "audit_events" in payload["preserved"]
    assert "app_settings" in payload["preserved"]

    assert db.fetch_many("tasks", limit=10) == []
    assert db.fetch_many("tool_results", limit=10) == []
    assert db.fetch_many("memories", limit=10) == []
    assert not list((tmp_path / "diagnostic-packages").glob("*.json"))
    # Settings survive a default erase.
    assert db.get_settings_overrides().get("preferred_mode") == "privacy"

    verification = verify_chain(limit=None)
    assert verification["ok"] is True
    events = db.fetch_many("audit_events", limit=50)
    erase_events = [e for e in events if e.get("event_type") == "privacy.local_data_erased"]
    assert len(erase_events) == 1

    # The response must not leak absolute local paths or user content.
    encoded = json.dumps(payload, ensure_ascii=False)
    assert str(tmp_path) not in encoded
    assert "private goal sample" not in encoded
    assert "private preference" not in encoded


def test_erase_with_include_settings_clears_settings_tables(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    client = TestClient(create_app())

    response = client.post(
        "/api/system/privacy/erase-local-data",
        json={"confirm": "erase-local-data", "include_settings": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted"]["rows_by_table"]["app_settings"] == 1
    assert "app_settings" not in payload["preserved"]
    assert db.get_settings_overrides() == {}
    assert verify_chain(limit=None)["ok"] is True
