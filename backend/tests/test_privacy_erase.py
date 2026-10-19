"""PIPL/GDPR local data deletion entry (market-readiness checklist #14).

POST /api/system/privacy/erase-local-data must erase locally stored user
content and exported diagnostic packages, preserve the tamper-evident audit
chain (appending an erase event), and refuse to run without an explicit
confirmation phrase.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest
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


def test_erase_clears_live_sessions_and_rejects_old_snapshots(monkeypatch, tmp_path):
    from app.core.session_context import SessionContextStore, SessionSummaryConflictError

    _setup_env(monkeypatch, tmp_path)
    stores = [SessionContextStore(session_id="global"), SessionContextStore(session_id="worker")]
    snapshots = []
    for store in stores:
        store.current.notes = ["ERASED_PRIVATE_NOTE"]
        store.save()
        snapshots.append(store.current.model_copy(deep=True))
    response = TestClient(create_app()).post(
        ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers()
    )
    assert response.status_code == 200
    for store, snapshot in zip(stores, snapshots, strict=True):
        assert store.planning_context()["notes"] == []
        store.save()  # The callback already held by the lifespan must be safe.
        with pytest.raises(SessionSummaryConflictError, match="erased"):
            store.save(snapshot)
    assert "ERASED_PRIVATE_NOTE" not in str(db.fetch_many("session_contexts"))
    stores[0].learn_preference("theme", "dark")
    assert stores[0].planning_context()["learned_preferences"]["theme"] == "dark"


def test_erase_removes_managed_content_without_deleting_originals(monkeypatch, tmp_path):
    from app.llm.registry import get_effective_settings
    from app.orchestration.result_budget import persist_large_result
    from app.tools.managed_backups import create_managed_backup

    _setup_env(monkeypatch, tmp_path)
    original = tmp_path / "user-file.txt"
    original.write_text("ORIGINAL_PRIVATE_FILE", encoding="utf-8")
    backup = Path(create_managed_backup(original)["path"])
    result = persist_large_result(get_effective_settings(), "task-erase", "result-erase", "file.read", "PRIVATE_RESULT")
    response = TestClient(create_app()).post(
        ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers()
    )
    assert response.status_code == 200
    assert not Path(result.path).exists()
    assert not backup.exists()
    assert original.read_text(encoding="utf-8") == "ORIGINAL_PRIVATE_FILE"


def test_erase_reports_managed_file_cleanup_failure(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    target = tmp_path / "file-tool-backups" / "locked.bak"
    target.parent.mkdir()
    target.write_text("LOCKED_PRIVATE_COPY", encoding="utf-8")
    original_unlink = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == target:
            raise PermissionError("synthetic locked file")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    response = TestClient(create_app()).post(
        ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers()
    )
    assert response.status_code == 500
    assert target.exists()
    assert str(tmp_path) not in response.text


@pytest.mark.parametrize("managed_path", ["file-tool-backups", "tasks/task-link/tool-results"])
def test_erase_never_follows_managed_directory_links(monkeypatch, tmp_path, managed_path):
    from test_file_tools_path_sandbox import _create_directory_escape_link, _remove_escape_link

    data = tmp_path / "data"
    data.mkdir()
    _setup_env(monkeypatch, data)
    original_dir = tmp_path / "originals"
    original_dir.mkdir()
    original = original_dir / "original.txt"
    original.write_text("MUST_PRESERVE_ORIGINAL", encoding="utf-8")
    link = data / managed_path
    link.parent.mkdir(parents=True, exist_ok=True)
    _create_directory_escape_link(link, original_dir)
    try:
        response = TestClient(create_app()).post(
            ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers()
        )
        assert response.status_code == 500
        assert original.read_text(encoding="utf-8") == "MUST_PRESERVE_ORIGINAL"
        assert str(original_dir) not in response.text
    finally:
        _remove_escape_link(link)


def _setup_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "audit-test-secret")
    monkeypatch.setenv("LENGRVIS_NATIVE_CONFIRMATION_SECRET", TEST_NATIVE_CONFIRMATION_SECRET)
    db.init_db()


def test_erase_refuses_in_flight_backup_and_succeeds_after_it_finishes(monkeypatch, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.tools import managed_backups

    _setup_env(monkeypatch, tmp_path)
    original = tmp_path / "original.txt"
    original.write_text("PRIVATE_ORIGINAL", encoding="utf-8")
    entered, release = threading.Event(), threading.Event()
    copy = managed_backups.shutil.copy2

    def blocked_copy(src, dst):
        entered.set()
        assert release.wait(10)
        return copy(src, dst)

    monkeypatch.setattr(managed_backups.shutil, "copy2", blocked_copy)
    client = TestClient(create_app())
    with ThreadPoolExecutor(max_workers=1) as executor:
        backup = executor.submit(managed_backups.create_managed_backup, original)
        try:
            assert entered.wait(10)
            response = client.post(ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers())
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "local_data_busy"
        finally:
            release.set()
        backup_path = Path(backup.result()["path"])
    response = client.post(ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers())
    assert response.status_code == 200
    assert not backup_path.exists()
    assert original.read_text(encoding="utf-8") == "PRIVATE_ORIGINAL"


def test_erase_excludes_an_in_flight_mutation_request(monkeypatch, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    _setup_env(monkeypatch, tmp_path)
    app = create_app()
    entered, release = threading.Event(), threading.Event()

    @app.post("/api/erase-regression-work")
    def write_after_wait():
        entered.set()
        assert release.wait(10)
        db.upsert_model("tasks", Task(user_goal="work started before erase"))
        return {"ok": True}

    client = TestClient(app)
    with ThreadPoolExecutor(max_workers=1) as executor:
        work = executor.submit(client.post, "/api/erase-regression-work")
        try:
            assert entered.wait(10)
            response = client.post(ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers())
            assert response.status_code == 409
        finally:
            release.set()
        assert work.result().status_code == 200
    assert (
        client.post(ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers()).status_code == 200
    )
    assert db.fetch_many("tasks") == []


@pytest.mark.parametrize(
    "endpoint", ["/api/runs", "/api/runs/{id}", "/api/runs/{id}/timeline", "/api/runs/{id}/progress"]
)
def test_erase_excludes_run_queries_that_reconcile_task_state(monkeypatch, tmp_path, endpoint):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from app.core.schemas import Run, RunPhase
    from app.security.desktop_api import desktop_api_token_headers
    from app.services import run_service

    _setup_env(monkeypatch, tmp_path)
    task = Task(user_goal="PRIVATE_OLD_REQUEST", status=TaskPhase.COMPLETED, final_summary="PRIVATE_OLD_SUMMARY")
    run = Run(message=task.user_goal, task_id=task.id, phase=RunPhase.RUNNING)
    db.upsert_model("tasks", task)
    db.upsert_model("runs", run)
    entered, release = threading.Event(), threading.Event()
    update_run = run_service._update_run

    def blocked_update(current, **kwargs):
        entered.set()
        assert release.wait(10)
        return update_run(current, **kwargs)

    monkeypatch.setattr(run_service, "_update_run", blocked_update)
    client = TestClient(create_app())
    headers = desktop_api_token_headers()
    with ThreadPoolExecutor(max_workers=1) as executor:
        query = executor.submit(client.get, endpoint.format(id=run.id), headers=headers)
        try:
            assert entered.wait(10)
            response = client.post(ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers())
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "local_data_busy"
        finally:
            release.set()
        assert query.result().status_code == 200
    assert (
        client.post(ERASE_ENDPOINT, json={"confirm": "erase-local-data"}, headers=_erase_headers()).status_code == 200
    )
    assert db.fetch_many("runs") == []
    assert db.fetch_many("run_events") == []
    assert client.get(endpoint.format(id=run.id), headers=headers).status_code == (
        200 if endpoint == "/api/runs" else 404
    )


def _seed_user_data(tmp_path) -> None:
    db.upsert_model(
        "tasks", Task(user_goal="private goal sample", status=TaskPhase.COMPLETED, phase=TaskPhase.COMPLETED)
    )
    db.upsert_model("tool_results", ToolResult(tool_call_id="tool_sample", ok=True, output={"note": "private"}))
    db.set_setting("preferred_mode", "privacy")
    db.upsert_memory({"id": "mem_sample", "content": "remember my private preference", "kind": "fact"})
    timestamp = db._now_iso()
    approval_data = json.dumps(
        {
            "id": "approval_sample",
            "task_id": "task_privacy_erase",
            "message": "approve private operation",
            "status": "pending",
            "created_at": timestamp,
        }
    )
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO approvals (id, task_id, step_id, data, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("approval_sample", "task_privacy_erase", "step_sample", approval_data, "pending", timestamp),
        )
        device_data = json.dumps(
            {
                "id": "device_sample",
                "device_id": "device_sample",
                "device_name": "Private phone",
                "status": "active",
                "token_epoch": 0,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        conn.execute(
            "INSERT INTO mobile_devices (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("device_sample", device_data, timestamp, timestamp),
        )
        conn.execute(
            """
            INSERT INTO device_credentials
                (id, device_id, credential_type, status, data, created_at, updated_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "credential_sample",
                "device_sample",
                "paired_device",
                "active",
                json.dumps({"public_key_thumbprint": "private-thumbprint"}),
                timestamp,
                timestamp,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO token_families
                (id, device_id, credential_id, status, current_generation, expires_at, data,
                 created_at, updated_at, revoked_at, reuse_detected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "family_sample",
                "device_sample",
                "credential_sample",
                "active",
                0,
                "2099-01-01T00:00:00+00:00",
                json.dumps({"private": "family metadata"}),
                timestamp,
                timestamp,
                None,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO mobile_refresh_tokens
                (id, family_id, device_id, generation, secret_hash, status, expires_at, data,
                 created_at, updated_at, used_at, replaced_by_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "refresh_sample",
                "family_sample",
                "device_sample",
                0,
                "private-refresh-hash",
                "active",
                "2099-01-01T00:00:00+00:00",
                json.dumps({"private": "refresh metadata"}),
                timestamp,
                timestamp,
                None,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO intent_capsules
                (id, task_id, plan_revision, status, expires_at, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "capsule_sample",
                "task_privacy_erase",
                1,
                "active",
                "2099-01-01T00:00:00+00:00",
                json.dumps({"user_goal_digest": "private-goal-digest"}),
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO run_budget_ledgers (id, run_id, status, version, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "budget_sample",
                "run_privacy_erase",
                "active",
                1,
                json.dumps({"private": "budget metadata"}),
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO automation_templates (id, name, enabled, current_version, data, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "template_sample",
                "Private workflow",
                1,
                1,
                json.dumps({"goal": "private automation goal"}),
                timestamp,
                timestamp,
            ),
        )
    db.store_sensitive_record_integrity("approvals", "approval_sample", approval_data)
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
    assert payload["deleted"]["rows_by_table"]["approvals"] == 1
    assert payload["deleted"]["rows_by_table"]["mobile_refresh_tokens"] == 1
    assert payload["deleted"]["rows_by_table"]["token_families"] == 1
    assert payload["deleted"]["rows_by_table"]["device_credentials"] == 1
    assert payload["deleted"]["rows_by_table"]["intent_capsules"] == 1
    assert payload["deleted"]["rows_by_table"]["run_budget_ledgers"] == 1
    assert payload["deleted"]["rows_by_table"]["automation_templates"] == 1
    assert payload["deleted"]["rows_by_table"]["memory_namespace"] == 1
    assert payload["deleted"]["diagnostic_packages"] == 1
    assert "audit_events" in payload["preserved"]
    assert "app_settings" in payload["preserved"]

    assert db.fetch_many("tasks", limit=10) == []
    assert db.fetch_many("tool_results", limit=10) == []
    assert db.fetch_many("memories", limit=10) == []
    for table in (
        "mobile_refresh_tokens",
        "token_families",
        "device_credentials",
        "intent_capsules",
        "run_budget_ledgers",
        "automation_templates",
        "memory_active_successors",
        "memory_quarantine",
        "memory_namespace",
    ):
        assert _table_count(table) == 0
    with db.connect() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM sensitive_record_integrity WHERE table_name = 'approvals' AND record_id = ?",
                ("approval_sample",),
            ).fetchone()
            is None
        )
        # The presence ledger must not retain rows for erased tables: leftover
        # rows leak that a record with that id/created_at once existed and make
        # the integrity check report the record as missing.
        assert (
            conn.execute("SELECT COUNT(*) FROM sensitive_record_presence WHERE table_name = 'approvals'").fetchone()[0]
            == 0
        )
    # A compliant erase must leave the integrity check passing; otherwise
    # fail-closed/commercial mode would block all local writes after erase.
    assert db.sensitive_integrity_check()["ok"] is True
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


def test_erase_rolls_back_all_database_deletes_when_verification_transaction_fails(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    _seed_user_data(tmp_path)
    with db.connect() as conn:
        conn.execute(
            """
            CREATE TRIGGER privacy_erase_test_block
            BEFORE DELETE ON tasks
            BEGIN
                SELECT RAISE(ABORT, 'privacy erase test failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="privacy erase test failure"):
        db.erase_local_user_data()

    assert _table_count("tasks") == 1
    assert _table_count("tool_results") == 1
    assert _table_count("mobile_refresh_tokens") == 1
    assert _table_count("token_families") == 1
    assert _table_count("device_credentials") == 1


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
    return (
        base64.urlsafe_b64encode(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        .decode("ascii")
        .rstrip("=")
    )


def _table_count(table: str) -> int:
    with db.connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
