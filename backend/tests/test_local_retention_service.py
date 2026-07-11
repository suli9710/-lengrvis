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
    orphan_tool_call_id = "tool_orphan_retention"
    orphan_approval_id = "approval_orphan_retention"

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
        conn.execute(
            "INSERT INTO tool_calls (id, task_id, step_id, data, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                orphan_tool_call_id,
                "task_missing_retention",
                "step_orphan",
                json.dumps({"args": {"value": secret}}),
                old_at,
            ),
        )
        conn.execute(
            "INSERT INTO tool_results (id, tool_call_id, data, created_at) VALUES (?, ?, ?, ?)",
            (
                "result_orphan_retention",
                orphan_tool_call_id,
                json.dumps({"output": {"value": secret}}),
                old_at,
            ),
        )
        conn.execute(
            "INSERT INTO tool_results (id, tool_call_id, data, created_at) VALUES (?, ?, ?, ?)",
            (
                "result_missing_call_retention",
                "tool_missing_retention",
                json.dumps({"output": {"value": secret}}),
                old_at,
            ),
        )
        orphan_approval_data = json.dumps(
            {
                "id": orphan_approval_id,
                "task_id": "task_missing_retention",
                "message": f"approve orphan {secret}",
                "status": "approved",
                "created_at": old_at,
            }
        )
        conn.execute(
            "INSERT INTO approvals (id, task_id, step_id, data, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                orphan_approval_id,
                "task_missing_retention",
                "step_orphan",
                orphan_approval_data,
                "approved",
                old_at,
            ),
        )
        for memory_id, content, data, created_at in (
            (
                "mem_old_quarantined",
                secret,
                {
                    "id": "mem_old_quarantined",
                    "content": secret,
                    "kind": "fact",
                    "source": "agent",
                    "state": "quarantined",
                    "user_confirmed": False,
                    "created_at": old_at,
                },
                old_at,
            ),
            (
                "mem_recent_quarantined",
                "recent quarantine",
                {
                    "id": "mem_recent_quarantined",
                    "content": "recent quarantine",
                    "kind": "fact",
                    "source": "agent",
                    "state": "quarantined",
                    "user_confirmed": False,
                    "created_at": recent_at,
                },
                recent_at,
            ),
            (
                "mem_active_confirmed",
                "explicit long term preference",
                {
                    "id": "mem_active_confirmed",
                    "content": "explicit long term preference",
                    "kind": "preference",
                    "source": "user",
                    "state": "active",
                    "user_confirmed": True,
                    "created_at": old_at,
                },
                old_at,
            ),
            (
                "mem_legacy_user",
                "legacy explicit preference",
                {
                    "id": "mem_legacy_user",
                    "content": "legacy explicit preference",
                    "kind": "preference",
                    "source": "user",
                    "created_at": old_at,
                },
                old_at,
            ),
            (
                "mem_expired_confirmed",
                secret,
                {
                    "id": "mem_expired_confirmed",
                    "content": secret,
                    "kind": "fact",
                    "source": "user",
                    "state": "active",
                    "user_confirmed": True,
                    "expires_at": old_at,
                    "created_at": recent_at,
                },
                recent_at,
            ),
        ):
            conn.execute(
                """
                INSERT INTO memories (id, kind, content, tags, task_id, embedding, data, created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (memory_id, data["kind"], content, "[]", "", None, json.dumps(data), created_at, created_at),
            )
    db.store_sensitive_record_integrity("approvals", old_approval_id, approval_data)
    db.store_sensitive_record_integrity("approvals", orphan_approval_id, orphan_approval_data)
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
    assert result["counts"]["memories_deleted"] == 2
    assert result["memory_review_retention_days"] == 30
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
    assert {row["id"] for row in db.fetch_many("memories", limit=20)} == {
        "mem_recent_quarantined",
        "mem_active_confirmed",
        "mem_legacy_user",
    }
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


def test_cleanup_uses_authoritative_memory_review_state() -> None:
    now = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    old_at = (now - timedelta(days=31)).isoformat()
    recent_at = (now - timedelta(days=1)).isoformat()
    memory_ids = {
        "old_quarantine",
        "recent_quarantine",
        "old_conflict",
        "recent_conflict",
        "normalized_expired",
        "authoritative_active",
    }
    for memory_id in memory_ids:
        db.upsert_memory(
            {
                "id": memory_id,
                "kind": "fact",
                "content": memory_id,
                "source": "user",
                "state": "active",
                "user_confirmed": True,
                "conflict_status": "none",
                "created_at": old_at,
                "last_used_at": old_at,
            }
        )

    with db.connect() as conn:
        for memory_id, updated_at in (
            ("old_quarantine", old_at),
            ("recent_quarantine", recent_at),
        ):
            conn.execute(
                """
                UPDATE memory_quarantine
                SET state = 'quarantined', user_confirmed = 0, updated_at = ?
                WHERE memory_id = ?
                """,
                (updated_at, memory_id),
            )
        for memory_id, updated_at in (
            ("old_conflict", old_at),
            ("recent_conflict", recent_at),
        ):
            conn.execute(
                """
                UPDATE memory_namespace
                SET conflict_status = 'conflicting', updated_at = ?
                WHERE memory_id = ?
                """,
                (updated_at, memory_id),
            )
        conn.execute(
            "UPDATE memory_quarantine SET expires_at = ?, updated_at = ? WHERE memory_id = ?",
            (old_at, recent_at, "normalized_expired"),
        )
        stale_payload = {
            "id": "authoritative_active",
            "kind": "fact",
            "content": "authoritative_active",
            "state": "quarantined",
            "user_confirmed": False,
            "conflict_status": "conflicting",
            "created_at": old_at,
        }
        conn.execute(
            "UPDATE memories SET data = ? WHERE id = ?",
            (json.dumps(stale_payload), "authoritative_active"),
        )

    result = cleanup_expired_task_details(
        now=now,
        retention_days=30,
        memory_review_retention_days=30,
        vacuum=False,
    )

    assert result["counts"]["memories_deleted"] == 3
    assert {row["id"] for row in db.fetch_many("memories", limit=20)} == {
        "recent_quarantine",
        "recent_conflict",
        "authoritative_active",
    }
    with db.connect() as conn:
        for table in ("memory_quarantine", "memory_namespace"):
            deleted_metadata = conn.execute(
                f"SELECT memory_id FROM {table} WHERE memory_id IN (?, ?, ?)",  # noqa: S608
                ("old_quarantine", "old_conflict", "normalized_expired"),
            ).fetchall()
            assert deleted_metadata == []


def _count(table: str) -> int:
    with db.connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
