from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core import db
from app.core.audit import record
from app.services.local_retention_service import EXPIRED_CONTENT_MARKER, cleanup_expired_task_details


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_AUDIT_HMAC_SECRET", "retention-test-audit-secret")
    db.reset_audit_caches()
    db.init_db()
    yield
    db.reset_audit_caches()


def test_cleanup_expires_business_content_but_preserves_status_and_audit_chain(tmp_path: Path):
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    old_at = (now - timedelta(days=31)).isoformat()
    recent_at = (now - timedelta(days=1)).isoformat()
    secret = "customer-secret-field-value"
    old_task_id = "task_old_retention"
    recent_task_id = "task_recent_retention"
    active_task_id = "task_active_retention"
    old_run_id = "run_old_retention"
    old_tool_call_id = "tool_old_retention"
    old_approval_id = "approval_old_retention"

    old_task = {
        "id": old_task_id,
        "user_goal": f"submit {secret}",
        "status": "completed",
        "phase": "completed",
        "execution_stage": "idle",
        "mode": "efficiency",
        "final_summary": f"submitted {secret}",
        "metadata": {"field_value": secret},
        "created_at": old_at,
        "updated_at": old_at,
    }
    recent_task = {
        **old_task,
        "id": recent_task_id,
        "user_goal": "recent task body",
        "final_summary": "recent summary",
        "metadata": {"scope": "recent"},
        "created_at": recent_at,
        "updated_at": recent_at,
    }
    old_run = {
        "id": old_run_id,
        "message": f"run {secret}",
        "mode": "efficiency",
        "requested_engine": "auto",
        "engine": "os",
        "phase": "completed",
        "task_id": old_task_id,
        "state": {"field_value": secret},
        "error": "",
        "created_at": old_at,
        "updated_at": old_at,
    }
    active_task = {
        **old_task,
        "id": active_task_id,
        "user_goal": "active task must remain available",
        "status": "execution",
        "phase": "execution",
        "final_summary": "",
        "metadata": {"scope": "active"},
    }
    approval_data = json.dumps(
        {
            "id": old_approval_id,
            "task_id": old_task_id,
            "message": f"approve {secret}",
            "status": "approved",
            "created_at": old_at,
        }
    )

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO tasks (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (old_task_id, json.dumps(old_task), old_at, old_at),
        )
        conn.execute(
            "INSERT INTO tasks (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (recent_task_id, json.dumps(recent_task), recent_at, recent_at),
        )
        conn.execute(
            "INSERT INTO tasks (id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (active_task_id, json.dumps(active_task), old_at, old_at),
        )
        conn.execute(
            "INSERT INTO runs (id, task_id, engine, phase, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (old_run_id, old_task_id, "os", "completed", json.dumps(old_run), old_at, old_at),
        )
        conn.execute(
            "INSERT INTO plans (id, task_id, data, created_at) VALUES (?, ?, ?, ?)",
            ("plan_old", old_task_id, json.dumps({"goal": secret}), old_at),
        )
        conn.execute(
            "INSERT INTO plans (id, task_id, data, created_at) VALUES (?, ?, ?, ?)",
            ("plan_recent", recent_task_id, json.dumps({"goal": "recent"}), recent_at),
        )
        conn.execute(
            "INSERT INTO agent_messages (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
            ("msg_old", old_task_id, "step_old", json.dumps({"content": secret}), old_at),
        )
        conn.execute(
            "INSERT INTO run_events (id, run_id, name, sequence, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("event_old", old_run_id, "run.completed", 1, json.dumps({"payload": secret}), old_at),
        )
        conn.execute(
            "INSERT INTO safety_reviews (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
            ("review_old", old_task_id, "step_old", json.dumps({"reason": secret}), old_at),
        )
        conn.execute(
            "INSERT INTO tool_calls (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (old_tool_call_id, old_task_id, "step_old", json.dumps({"args": {"value": secret}}), old_at),
        )
        conn.execute(
            "INSERT INTO tool_results (id, tool_call_id, data, created_at) VALUES (?, ?, ?, ?)",
            ("result_old", old_tool_call_id, json.dumps({"output": {"value": secret}}), old_at),
        )
        conn.execute(
            "INSERT INTO approvals (id, task_id, step_id, data, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (old_approval_id, old_task_id, "step_old", approval_data, "approved", old_at),
        )
        conn.execute(
            """
            INSERT INTO task_recordings
                (id, task_id, step_id, phase, file_name, mime_type, width, height, image, data, captured_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rec_old",
                old_task_id,
                "step_old",
                "before",
                "old.png",
                "image/png",
                1,
                1,
                secret.encode("utf-8"),
                json.dumps({"file_name": "old.png"}),
                old_at,
                old_at,
            ),
        )
        conn.execute(
            "INSERT INTO chat_messages (id, data, created_at) VALUES (?, ?, ?)",
            ("chat_old", json.dumps({"content": secret}), old_at),
        )
        conn.execute(
            "INSERT INTO chat_messages (id, data, created_at) VALUES (?, ?, ?)",
            ("chat_recent", json.dumps({"content": "recent chat"}), recent_at),
        )
    db.store_sensitive_record_integrity("approvals", old_approval_id, approval_data)
    record("task.completed", "pytest", {"status": "completed"}, task_id=old_task_id)
    original_audit_count = _count("audit_events")

    diagnostic_dir = tmp_path / "data" / "diagnostic-packages"
    diagnostic_dir.mkdir(parents=True)
    old_diagnostic = diagnostic_dir / "lengrvis-diagnostics-old.json"
    old_diagnostic.write_text(json.dumps({"value": secret}), encoding="utf-8")
    old_timestamp = (now - timedelta(days=31)).timestamp()
    os.utime(old_diagnostic, (old_timestamp, old_timestamp))

    result = cleanup_expired_task_details(now=now, retention_days=30)

    assert result["audit_chain_preserved"] is True
    assert result["vacuumed"] is True
    assert result["counts"]["tasks_expired"] == 1
    assert result["counts"]["runs_expired"] == 1
    assert result["counts"]["task_recordings_deleted"] == 1
    assert result["counts"]["diagnostic_packages_deleted"] == 1
    assert not old_diagnostic.exists()

    expired_task = db.fetch_one("tasks", old_task_id)
    assert expired_task is not None
    assert expired_task["status"] == "completed"
    assert expired_task["user_goal"] == EXPIRED_CONTENT_MARKER
    assert expired_task["final_summary"] == EXPIRED_CONTENT_MARKER
    assert expired_task["metadata"]["retention"]["details_expired"] is True
    assert db.fetch_one("tasks", recent_task_id)["user_goal"] == "recent task body"
    assert db.fetch_one("tasks", active_task_id)["user_goal"] == "active task must remain available"
    assert db.fetch_one("runs", old_run_id)["message"] == EXPIRED_CONTENT_MARKER

    for table in (
        "agent_messages",
        "run_events",
        "task_recordings",
        "safety_reviews",
        "tool_calls",
        "tool_results",
        "approvals",
    ):
        assert _count(table) == 0
    assert _count("plans") == 1
    assert _count("chat_messages") == 1
    assert _count("audit_events") == original_audit_count + 1
    with db.connect() as conn:
        proof = conn.execute(
            "SELECT 1 FROM sensitive_record_integrity WHERE table_name = 'approvals' AND record_id = ?",
            (old_approval_id,),
        ).fetchone()
    assert proof is None

    for database_file in (tmp_path / "data").glob("lengrvis.db*"):
        assert secret.encode("utf-8") not in database_file.read_bytes()

    repeated = cleanup_expired_task_details(now=now, retention_days=30, vacuum=False)
    assert repeated["rows_changed"] == 0
    assert repeated["counts"]["tasks_expired"] == 0


def _count(table: str) -> int:
    with db.connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
