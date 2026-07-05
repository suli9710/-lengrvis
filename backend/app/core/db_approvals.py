from __future__ import annotations

import json
from typing import Any

from app.core import db


def claim_approval_for_execution(approval_id: str, consumed_at: str) -> dict[str, Any] | None:
    """Atomically mark an approved approval as consumed before side effects run."""
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ? AND status = ?",
            (approval_id, "approved"),
        ).fetchone()
        if not row:
            return None
        db._require_sensitive_record_integrity(conn, "approvals", approval_id, row["data"])
        data = json.loads(row["data"])
        if data.get("consumed_at"):
            return None
        data["consumed_at"] = consumed_at
        stored = db._json(data)
        cursor = conn.execute(
            """
            UPDATE approvals
            SET data = ?
            WHERE id = ?
              AND status = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (stored, approval_id, "approved"),
        )
        if cursor.rowcount != 1:
            return None
        db._store_sensitive_record_integrity(conn, "approvals", approval_id, stored)
    return data


def expire_approval_if_pending(approval_id: str, expired_at: str, reason: str = "") -> dict[str, Any] | None:
    """Atomically expire one pending, unconsumed approval."""
    return expire_approval_if_unconsumed(approval_id, expired_at, reason, statuses={"pending"})


def expire_approval_if_unconsumed(
    approval_id: str,
    expired_at: str,
    reason: str = "",
    *,
    statuses: set[str] | None = None,
) -> dict[str, Any] | None:
    """Atomically expire one unconsumed approval in an allowed status."""
    allowed_statuses = statuses or {"pending", "approved"}
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        db._require_sensitive_record_integrity(conn, "approvals", approval_id, row["data"])
        data = json.loads(row["data"])
        current_status = str(data.get("status") or "")
        if current_status not in allowed_statuses or data.get("consumed_at"):
            return None
        data["status"] = "expired"
        data["decided_at"] = expired_at
        if reason:
            data["expired_reason"] = reason
        placeholders = ",".join("?" for _ in allowed_statuses)
        query_template = """
            UPDATE approvals
            SET data = ?,
                status = ?
            WHERE id = ?
              AND status IN ({status_placeholders})
              AND json_extract(data, '$.consumed_at') IS NULL
        """
        query = query_template.format(status_placeholders=placeholders)  # noqa: S608
        stored = db._json(data)
        cursor = conn.execute(
            query,
            (stored, "expired", approval_id, *sorted(allowed_statuses)),
        )
        if cursor.rowcount != 1:
            return None
        db._store_sensitive_record_integrity(conn, "approvals", approval_id, stored)
    return data


def expire_pending_approvals_for_task(task_id: str, expired_at: str, reason: str = "") -> list[dict[str, Any]]:
    """Atomically expire all pending, unconsumed approvals for a task."""
    expired: list[dict[str, Any]] = []
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, data FROM approvals WHERE task_id = ? AND status = ?",
            (task_id, "pending"),
        ).fetchall()
        for row in rows:
            db._require_sensitive_record_integrity(conn, "approvals", row["id"], row["data"])
            data = json.loads(row["data"])
            if data.get("status") != "pending" or data.get("consumed_at"):
                continue
            data["status"] = "expired"
            data["decided_at"] = expired_at
            if reason:
                data["expired_reason"] = reason
            stored = db._json(data)
            cursor = conn.execute(
                """
                UPDATE approvals
                SET data = ?,
                    status = ?
                WHERE id = ?
                  AND status = ?
                  AND json_extract(data, '$.status') = ?
                  AND json_extract(data, '$.consumed_at') IS NULL
                """,
                (stored, "expired", row["id"], "pending", "pending"),
            )
            if cursor.rowcount == 1:
                db._store_sensitive_record_integrity(conn, "approvals", row["id"], stored)
                expired.append(data)
    return expired


def count_pending_remote_input_approvals(grant_id: str, device_id: str) -> int:
    """Count active pending remote-input approvals for one grant/device binding."""
    normalized_grant_id = str(grant_id or "").strip()
    normalized_device_id = str(device_id or "").strip()
    if not normalized_grant_id or not normalized_device_id:
        return 0
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT id, data
            FROM approvals
            WHERE status = ?
              AND json_extract(data, '$.status') = ?
              AND json_extract(data, '$.source') = ?
              AND json_extract(data, '$.source_grant_id') = ?
              AND json_extract(data, '$.source_device_id') = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (
                "pending",
                "pending",
                "remote_input",
                normalized_grant_id,
                normalized_device_id,
            ),
        ).fetchall()
        for row in rows:
            db._require_sensitive_record_integrity(conn, "approvals", row["id"], row["data"])
    return len(rows)


def decide_approval_atomically(approval_id: str, status: str, decided_at: str) -> dict[str, Any] | None:
    """Atomically move a pending, unconsumed approval to a terminal decision."""
    if status not in {"approved", "rejected"}:
        raise ValueError(f"Unsupported approval decision status: {status}")
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT data FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if not row:
            return None
        db._require_sensitive_record_integrity(conn, "approvals", approval_id, row["data"])
        data = json.loads(row["data"])
        if data.get("status") != "pending" or data.get("consumed_at"):
            return None
        data["status"] = status
        data["decided_at"] = decided_at
        stored = db._json(data)
        cursor = conn.execute(
            """
            UPDATE approvals
            SET data = ?,
                status = ?
            WHERE id = ?
              AND status = ?
              AND json_extract(data, '$.status') = ?
              AND json_extract(data, '$.consumed_at') IS NULL
            """,
            (stored, status, approval_id, "pending", "pending"),
        )
        if cursor.rowcount != 1:
            return None
        db._store_sensitive_record_integrity(conn, "approvals", approval_id, stored)
    return data
