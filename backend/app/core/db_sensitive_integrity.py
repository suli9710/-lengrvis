from __future__ import annotations

import hmac
import json
import sqlite3
from collections.abc import Iterator
from hashlib import sha256
from typing import Any

from app.config import get_env
from app.core import db

_STARTUP_SENSITIVE_INTEGRITY_STATUS: dict[str, Any] = {"ok": True, "checked": 0, "failures": []}


def store_sensitive_record_integrity(
    table: str,
    record_id: str,
    data: str,
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    if conn is not None:
        db._store_sensitive_record_integrity(conn, table, record_id, data)
        return
    with db.connect() as active_conn:
        db._begin_immediate_transaction(active_conn)
        db._store_sensitive_record_integrity(active_conn, table, record_id, data)


def require_sensitive_record_integrity(table: str, record_id: str, data: str) -> None:
    with db.connect() as conn:
        db._require_sensitive_record_integrity(conn, table, record_id, data)


def sensitive_integrity_check() -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    checked = 0
    with db.connect() as conn:
        db._ensure_sensitive_record_integrity_schema(conn)
        checks = (
            ("approvals", "SELECT id, data FROM approvals"),
            ("app_settings", "SELECT key AS id, value AS data FROM app_settings"),
            ("permission_policies", "SELECT id, data FROM permission_policies"),
            (
                "audit_chain_heads",
                "SELECT id, sequence, event_hash, event_id, created_at FROM audit_chain_heads",
            ),
        )
        for table, query in checks:
            try:
                rows = conn.execute(query).fetchall()
            except sqlite3.Error:
                continue
            for row in rows:
                checked += 1
                data = (
                    db._audit_chain_head_integrity_payload(
                        record_id=str(row["id"]),
                        sequence=int(row["sequence"] or 0),
                        event_hash=str(row["event_hash"] or ""),
                        event_id=str(row["event_id"] or ""),
                        created_at=str(row["created_at"] or ""),
                    )
                    if table == "audit_chain_heads"
                    else str(row["data"])
                )
                try:
                    db._require_sensitive_record_integrity(conn, table, str(row["id"]), data)
                except db.SensitiveRecordIntegrityError as exc:
                    failures.append({"table": table, "id": str(row["id"]), "reason": str(exc)})
    return {"ok": not failures, "checked": checked, "failures": failures}


def bootstrap_sensitive_record_integrity() -> dict[str, Any]:
    """Sign pre-existing local sensitive records once during startup migration."""
    failures: list[dict[str, str]] = []
    checked = 0
    bootstrap_completed = False
    with db.connect() as conn:
        db._ensure_sensitive_record_integrity_schema(conn)
        db._begin_immediate_transaction(conn)
        bootstrap_completed = db._sensitive_integrity_bootstrap_completed(conn)
        for table, row, data in iter_sensitive_record_rows(conn):
            checked += 1
            record_id = str(row["id"])
            if sensitive_record_integrity_row_exists(conn, table, record_id):
                try:
                    db._require_sensitive_record_integrity(conn, table, record_id, data)
                except db.SensitiveRecordIntegrityError as exc:
                    failures.append({"table": table, "id": record_id, "reason": str(exc)})
            elif bootstrap_completed:
                failures.append(
                    {
                        "table": table,
                        "id": record_id,
                        "reason": "Sensitive local record integrity proof missing",
                    }
                )
            else:
                db._store_sensitive_record_integrity(conn, table, record_id, data)
        if not failures and not bootstrap_completed:
            mark_sensitive_integrity_bootstrap_completed(conn)
    status = {"ok": not failures, "checked": checked, "failures": failures}
    set_startup_sensitive_integrity_status(status)
    return status


def set_startup_sensitive_integrity_status(status: dict[str, Any]) -> None:
    global _STARTUP_SENSITIVE_INTEGRITY_STATUS
    _STARTUP_SENSITIVE_INTEGRITY_STATUS = dict(status)


def get_startup_sensitive_integrity_status() -> dict[str, Any]:
    return dict(_STARTUP_SENSITIVE_INTEGRITY_STATUS)


def require_sensitive_integrity_ok() -> None:
    startup = get_startup_sensitive_integrity_status()
    if startup and startup.get("ok") is False:
        raise db.SensitiveRecordIntegrityError("Sensitive local record integrity check failed at startup")
    current = sensitive_integrity_check()
    set_startup_sensitive_integrity_status(current)
    if not current.get("ok"):
        failure = (current.get("failures") or [{}])[0]
        raise db.SensitiveRecordIntegrityError(
            f"Sensitive local record integrity check failed for {failure.get('table')}:{failure.get('id')}"
        )


def audit_fail_closed_enabled() -> bool:
    raw = str(get_env(db.AUDIT_FAIL_CLOSED_ENV_VAR) or "").strip().lower()
    commercial = str(get_env("LENGRVIS_COMMERCIAL_RELEASE") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"} or commercial in {"1", "true", "yes", "on"}


def audit_fail_closed_status() -> dict[str, Any]:
    audit_status = db.verify_audit_log()
    sensitive_status = sensitive_integrity_check()
    failures: list[dict[str, Any]] = []
    if not audit_status.get("ok"):
        failures.append(
            {
                "kind": "audit_chain",
                "reason": audit_status.get("failure_reason") or "audit_chain_invalid",
            }
        )
    if not sensitive_status.get("ok"):
        failure = (sensitive_status.get("failures") or [{}])[0]
        failures.append(
            {
                "kind": "sensitive_record_integrity",
                "table": failure.get("table"),
                "id": failure.get("id"),
                "reason": failure.get("reason") or "sensitive_record_integrity_invalid",
            }
        )
    return {
        "ok": not failures,
        "audit": audit_status,
        "sensitive_records": sensitive_status,
        "failures": failures,
    }


def require_audit_fail_closed_ok() -> None:
    status = audit_fail_closed_status()
    if status.get("ok"):
        return
    failure = (status.get("failures") or [{}])[0]
    raise db.SensitiveRecordIntegrityError(
        f"Audit fail-closed gate blocked local writes: {failure.get('kind')}:{failure.get('reason')}"
    )


def iter_sensitive_record_rows(conn: sqlite3.Connection) -> Iterator[tuple[str, sqlite3.Row, str]]:
    checks = (
        ("approvals", "SELECT id, data FROM approvals"),
        ("app_settings", "SELECT key AS id, value AS data FROM app_settings"),
        ("permission_policies", "SELECT id, data FROM permission_policies"),
        (
            "audit_chain_heads",
            "SELECT id, sequence, event_hash, event_id, created_at FROM audit_chain_heads",
        ),
    )
    for table, query in checks:
        try:
            rows = conn.execute(query).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            data = (
                db._audit_chain_head_integrity_payload(
                    record_id=str(row["id"]),
                    sequence=int(row["sequence"] or 0),
                    event_hash=str(row["event_hash"] or ""),
                    event_id=str(row["event_id"] or ""),
                    created_at=str(row["created_at"] or ""),
                )
                if table == "audit_chain_heads"
                else str(row["data"])
            )
            yield table, row, data


def ensure_sensitive_record_integrity_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {db.SENSITIVE_RECORD_INTEGRITY_TABLE} (
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            digest TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (table_name, record_id)
        )
        """
    )


def store_sensitive_record_integrity_locked(conn: sqlite3.Connection, table: str, record_id: str, data: str) -> None:
    if table not in db.SENSITIVE_RECORD_INTEGRITY_KINDS or not record_id:
        return
    ensure_sensitive_record_integrity_schema(conn)
    digest = sensitive_record_digest(table, record_id, data)
    conn.execute(
        """
        INSERT INTO sensitive_record_integrity (table_name, record_id, version, digest, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(table_name, record_id) DO UPDATE SET
            version=excluded.version,
            digest=excluded.digest,
            updated_at=excluded.updated_at
        """,
        (table, record_id, db.SENSITIVE_RECORD_INTEGRITY_VERSION, digest, db._now_iso()),
    )


def sensitive_record_integrity_row_exists(conn: sqlite3.Connection, table: str, record_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sensitive_record_integrity
        WHERE table_name = ? AND record_id = ?
        """,
        (table, record_id),
    ).fetchone()
    return row is not None


def sensitive_integrity_bootstrap_payload() -> str:
    return json.dumps({"version": db.SENSITIVE_RECORD_INTEGRITY_VERSION, "bootstrapped": True})


def sensitive_integrity_bootstrap_digest() -> str:
    return hmac.new(
        db._audit_hmac_secret().encode("utf-8"),
        sensitive_integrity_bootstrap_payload().encode("utf-8"),
        sha256,
    ).hexdigest()


def sensitive_integrity_bootstrap_completed(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT digest
        FROM sensitive_record_integrity
        WHERE table_name = '__meta__' AND record_id = 'bootstrap'
        """,
    ).fetchone()
    if not row:
        return False
    expected = sensitive_integrity_bootstrap_digest()
    return hmac.compare_digest(str(row["digest"] or ""), expected)


def mark_sensitive_integrity_bootstrap_completed(conn: sqlite3.Connection) -> None:
    ensure_sensitive_record_integrity_schema(conn)
    conn.execute(
        """
        INSERT INTO sensitive_record_integrity (table_name, record_id, version, digest, updated_at)
        VALUES ('__meta__', 'bootstrap', ?, ?, ?)
        ON CONFLICT(table_name, record_id) DO UPDATE SET
            version=excluded.version,
            digest=excluded.digest,
            updated_at=excluded.updated_at
        """,
        (db.SENSITIVE_RECORD_INTEGRITY_VERSION, sensitive_integrity_bootstrap_digest(), db._now_iso()),
    )


def require_sensitive_record_integrity_locked(conn: sqlite3.Connection, table: str, record_id: str, data: str) -> None:
    if table not in db.SENSITIVE_RECORD_INTEGRITY_KINDS or not record_id:
        return
    ensure_sensitive_record_integrity_schema(conn)
    row = conn.execute(
        """
        SELECT digest
        FROM sensitive_record_integrity
        WHERE table_name = ? AND record_id = ?
        """,
        (table, record_id),
    ).fetchone()
    expected = sensitive_record_digest(table, record_id, data)
    if not row:
        raise db.SensitiveRecordIntegrityError(
            f"Sensitive local record integrity proof missing for {table}:{record_id}"
        )
    actual = str(row["digest"] or "")
    if not hmac.compare_digest(actual, expected):
        raise db.SensitiveRecordIntegrityError(f"Sensitive local record integrity check failed for {table}:{record_id}")


def sensitive_record_digest(table: str, record_id: str, data: str) -> str:
    body = json.dumps(
        {
            "version": db.SENSITIVE_RECORD_INTEGRITY_VERSION,
            "table": table,
            "record_id": record_id,
            "data": data,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hmac.new(db._audit_hmac_secret().encode("utf-8"), body.encode("utf-8"), sha256).hexdigest()
