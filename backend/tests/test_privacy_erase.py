"""PIPL/GDPR local data deletion entry (market-readiness checklist #14).

POST /api/system/privacy/erase-local-data must erase locally stored user
content and exported diagnostic packages, preserve the tamper-evident audit
chain (appending an erase event), and refuse to run without an explicit
confirmation phrase.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from native_confirmation_helpers import (
    TEST_NATIVE_CONFIRMATION_SECRET,
    native_confirmation_headers,
    signed_native_confirmation_headers,
)

from app.core import db
from app.core.audit import record, verify_chain
from app.core.schemas import Task, ToolResult
from app.main import create_app
from app.orchestration.task_phase import TaskPhase
from app.security.native_confirmation import NATIVE_CONFIRMATION_PUBLIC_KEY_ENV

ERASE_ENDPOINT = "/api/system/privacy/erase-local-data"


def _setup_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_SECRET", TEST_NATIVE_CONFIRMATION_SECRET)
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


def test_erase_requires_native_confirmation(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    client = TestClient(create_app())

    response = client.post(ERASE_ENDPOINT, json={"confirm": "erase-local-data"})

    assert response.status_code == 403
    assert "Native confirmation proof is required" in response.json()["detail"]
    assert db.fetch_many("tasks", limit=10)


def test_erase_deletes_user_content_and_packages_preserving_audit_chain(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    client = TestClient(create_app())

    response = client.post(
        ERASE_ENDPOINT,
        json={"confirm": "erase-local-data"},
        headers=_erase_headers(),
    )

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
        ERASE_ENDPOINT,
        json={"confirm": "erase-local-data", "include_settings": True},
        headers=_erase_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted"]["rows_by_table"]["app_settings"] == 1
    assert "app_settings" not in payload["preserved"]
    assert db.get_settings_overrides() == {}
    assert verify_chain(limit=None)["ok"] is True


def test_erase_accepts_ed25519_native_confirmation_challenge(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, _public_key_b64(private_key))
    client = TestClient(create_app())
    payload = {"confirm": "erase-local-data"}

    challenge = client.post(f"{ERASE_ENDPOINT}/native-confirmation-challenge", json=payload)
    assert challenge.status_code == 200, challenge.text
    response = client.post(
        ERASE_ENDPOINT,
        json=payload,
        headers=signed_native_confirmation_headers(challenge.json(), private_key),
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert db.fetch_many("tasks", limit=10) == []


def test_erase_ed25519_challenge_rejects_changed_body_hash(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setenv(NATIVE_CONFIRMATION_PUBLIC_KEY_ENV, _public_key_b64(private_key))
    client = TestClient(create_app())
    payload = {"confirm": "erase-local-data", "include_settings": False}

    challenge = client.post(f"{ERASE_ENDPOINT}/native-confirmation-challenge", json=payload)
    assert challenge.status_code == 200, challenge.text
    response = client.post(
        ERASE_ENDPOINT,
        json={"confirm": "erase-local-data", "include_settings": True},
        headers=signed_native_confirmation_headers(challenge.json(), private_key),
    )

    assert response.status_code == 403
    assert "preview changed" in response.json()["detail"]
    assert db.fetch_many("tasks", limit=10)


def _erase_headers() -> dict[str, str]:
    return native_confirmation_headers(
        "erase_local_data",
        "local-data",
        endpoint=ERASE_ENDPOINT,
    )


def _public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return base64.urlsafe_b64encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii").rstrip("=")
