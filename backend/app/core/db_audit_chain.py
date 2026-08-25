from __future__ import annotations

import hmac
import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from app.config import get_base_settings, get_env
from app.core import db
from app.policy.redaction import redact_audit_storage_payload


def insert_audit_event(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    data = json.loads(model.model_dump_json()) if isinstance(model, BaseModel) else dict(model)
    return insert_audit_event_record(data)


def insert_audit_event_record(data: dict[str, Any]) -> dict[str, Any]:
    db.init_db()
    try:
        with db._EVENT_WRITE_LOCK, db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            stored = prepare_audit_event_locked(conn, data)
            conn.execute(
                """
                INSERT INTO audit_events (
                    id, task_id, event_type, actor, sequence, prev_hash,
                    event_hash, hmac, data, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored["id"],
                    stored.get("task_id"),
                    stored["event_type"],
                    stored["actor"],
                    stored["sequence"],
                    stored["prev_hash"],
                    stored["event_hash"],
                    stored["hmac"],
                    db._json(stored),
                    stored["created_at"],
                ),
            )
            store_audit_chain_head(stored["sequence"], stored["event_hash"], event_id=stored["id"])
    except Exception:  # noqa: BLE001 - broad-exception-boundary: any failed audit write invalidates the cached chain head.
        invalidate_audit_chain_head()
        raise
    return stored


def verify_audit_log(*, limit: int | None = None) -> dict[str, Any]:
    query = """
        SELECT id, task_id, event_type, actor, sequence, prev_hash, event_hash, hmac, data, created_at
        FROM audit_events
        WHERE sequence > 0
        ORDER BY sequence ASC, created_at ASC, id ASC
    """
    args: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        args = (max(1, int(limit)),)

    expected_prev = db.AUDIT_GENESIS_HASH
    checked = 0
    failures: list[dict[str, Any]] = []
    last_hash = expected_prev
    last_event_id: str | None = None
    last_sequence = 0
    persisted_head: dict[str, Any] | None = None
    external_anchor: dict[str, Any] | None = None
    missing_triggers: list[str] = []
    with db.connect() as conn:
        rows = conn.execute(query, args).fetchall()
        if limit is None:
            missing_triggers = missing_audit_append_only_triggers(conn)
            persisted_head = latest_persisted_audit_chain_head(conn)
            external_anchor = read_audit_anchor()

    for index, row in enumerate(rows, start=1):
        row_id = str(row["id"] or "")
        row_sequence = int(row["sequence"] or 0)
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError):
            failures.append({"index": index, "id": row_id, "sequence": row_sequence, "reason": "invalid_json"})
            break

        sequence = int(data.get("sequence") or row_sequence or 0)
        prev_hash = str(data.get("prev_hash") or row["prev_hash"] or "")
        event_hash = str(data.get("event_hash") or row["event_hash"] or "")
        event_hmac = str(data.get("hmac") or row["hmac"] or "")

        column_mismatch = audit_column_mismatch(row, data)
        if column_mismatch:
            failures.append(
                {
                    "index": index,
                    "id": row_id,
                    "sequence": sequence,
                    "reason": "stored_column_mismatch",
                    "fields": column_mismatch,
                }
            )
            break
        if sequence != index:
            failures.append(
                {"index": index, "id": row_id, "sequence": sequence, "reason": "sequence_gap", "expected": index}
            )
            break
        if prev_hash != expected_prev:
            failures.append({"index": index, "id": row_id, "sequence": sequence, "reason": "prev_hash_mismatch"})
            break

        unsigned = dict(data)
        unsigned["sequence"] = sequence
        unsigned["prev_hash"] = prev_hash
        unsigned["event_hash"] = ""
        unsigned["hmac"] = ""
        computed_hash = audit_event_hash(unsigned)
        computed_hmac = audit_event_hmac(computed_hash)
        if not hmac.compare_digest(event_hash, computed_hash):
            failures.append({"index": index, "id": row_id, "sequence": sequence, "reason": "event_hash_mismatch"})
            break
        if not hmac.compare_digest(event_hmac, computed_hmac):
            failures.append({"index": index, "id": row_id, "sequence": sequence, "reason": "hmac_mismatch"})
            break

        checked += 1
        last_hash = event_hash
        last_event_id = row_id
        last_sequence = sequence
        expected_prev = event_hash

    if not failures and limit is None and persisted_head is not None:
        anchored_sequence = int(persisted_head["sequence"] or 0)
        anchored_hash = str(persisted_head["event_hash"] or "")
        if anchored_sequence != last_sequence or anchored_hash != last_hash:
            failures.append(
                {
                    "index": checked + 1,
                    "id": persisted_head.get("event_id") or None,
                    "sequence": anchored_sequence,
                    "reason": "tail_truncated",
                    "expected_last_sequence": anchored_sequence,
                    "actual_last_sequence": last_sequence,
                }
            )

    if limit is None and missing_triggers:
        failures.append(
            {
                "index": checked + 1,
                "id": last_event_id,
                "sequence": last_sequence,
                "reason": "append_only_trigger_missing",
                "missing_triggers": missing_triggers,
            }
        )

    if not failures and limit is None and external_anchor is not None:
        if external_anchor.get("invalid"):
            failures.append(
                {
                    "index": checked + 1,
                    "id": None,
                    "sequence": 0,
                    "reason": "external_anchor_invalid",
                }
            )
        else:
            anchored_sequence = int(external_anchor.get("sequence") or 0)
            anchored_hash = str(external_anchor.get("event_hash") or "")
            if anchored_sequence != last_sequence or anchored_hash != last_hash:
                failures.append(
                    {
                        "index": checked + 1,
                        "id": external_anchor.get("event_id") or None,
                        "sequence": anchored_sequence,
                        "reason": "external_anchor_mismatch",
                        "expected_last_sequence": anchored_sequence,
                        "actual_last_sequence": last_sequence,
                    }
                )

    failure = failures[0] if failures else {}
    return {
        "ok": not failures,
        "checked": checked,
        "last_event_id": last_event_id,
        "last_sequence": last_sequence,
        "last_hash": last_hash,
        "failure_index": failure.get("index"),
        "failure_event_id": failure.get("id"),
        "failure_sequence": failure.get("sequence"),
        "failure_reason": str(failure.get("reason") or ""),
        "failures": failures,
        "anchor": external_anchor,
    }


def audit_column_mismatch(row: sqlite3.Row, data: dict[str, Any]) -> list[str]:
    mismatched: list[str] = []
    for field in ("id", "task_id", "event_type", "actor", "sequence", "prev_hash", "event_hash", "hmac", "created_at"):
        if field not in data:
            continue
        row_value = row[field]
        data_value = data.get(field)
        if field == "sequence":
            if int(data_value or 0) != int(row_value or 0):
                mismatched.append(field)
            continue
        if data_value is None and row_value is None:
            continue
        if str(data_value or "") != str(row_value or ""):
            mismatched.append(field)
    return mismatched


def missing_audit_append_only_triggers(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'trigger'
          AND name IN (?, ?, ?, ?)
        """,
        tuple(sorted(db.AUDIT_APPEND_ONLY_TRIGGERS)),
    ).fetchall()
    present = {str(row["name"]) for row in rows}
    return sorted(db.AUDIT_APPEND_ONLY_TRIGGERS - present)


def prepare_audit_event_locked(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    stored = storage_safe_audit_event(data)
    stored.setdefault("id", f"audit_{uuid4().hex}")
    stored["created_at"] = stored.get("created_at") or db._now_iso()

    hmac_secret = audit_hmac_secret()

    key = str(db.db_path())
    with db._AUDIT_CACHE_LOCK:
        head = db._AUDIT_CHAIN_HEADS.get(key)
        if head is None:
            persisted_head = latest_persisted_audit_chain_head(conn)
            if persisted_head is not None:
                sequence = int(persisted_head["sequence"] or 0) + 1
                prev_hash = str(persisted_head["event_hash"] or "")
            else:
                row = conn.execute(
                    """
                    SELECT sequence, event_hash
                    FROM audit_events
                    ORDER BY sequence DESC, created_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
                sequence = int(row["sequence"] or 0) + 1 if row else 1
                prev_hash = str(row["event_hash"] or "") if row else db.AUDIT_GENESIS_HASH
        else:
            sequence = head[0] + 1
            prev_hash = head[1]

        stored["sequence"] = sequence
        stored["prev_hash"] = prev_hash
        stored["event_hash"] = ""
        stored["hmac"] = ""
        event_hash = audit_event_hash(stored)
        stored["event_hash"] = event_hash
        stored["hmac"] = audit_event_hmac(event_hash, secret=hmac_secret)
        db._AUDIT_CHAIN_HEADS[key] = (sequence, event_hash)
    return stored


def storage_safe_audit_event(data: dict[str, Any]) -> dict[str, Any]:
    stored = dict(data)
    payload = stored.get("payload")
    if isinstance(payload, dict):
        redacted = redact_audit_storage_payload(payload)
        stored["payload"] = redacted if isinstance(redacted, dict) else {}
        restore_remote_input_approval_binding_ids(stored, payload)
    return stored


def restore_remote_input_approval_binding_ids(stored: dict[str, Any], original_payload: dict[str, Any]) -> None:
    if stored.get("event_type") != "remote.input.approval_requested":
        return
    payload = stored.get("payload")
    if not isinstance(payload, dict):
        return
    for key in ("approval_id", "device_id", "grant_id"):
        value = original_payload.get(key)
        if isinstance(value, str) and value:
            payload[key] = value


def store_audit_chain_head(sequence: int, event_hash: str, *, event_id: str = "") -> None:
    record_id = f"audit_head_{uuid4().hex}"
    created_at = db._now_iso()
    payload = audit_chain_head_integrity_payload(
        record_id=record_id,
        sequence=int(sequence),
        event_hash=str(event_hash),
        event_id=str(event_id or ""),
        created_at=created_at,
    )
    with db._AUDIT_CACHE_LOCK:
        db._AUDIT_CHAIN_HEADS[str(db.db_path())] = (int(sequence), str(event_hash))
    with db.connect() as conn:
        db._begin_immediate_transaction(conn)
        conn.execute(
            """
            INSERT INTO audit_chain_heads (id, sequence, event_hash, event_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (record_id, int(sequence), str(event_hash), str(event_id or ""), created_at),
        )
        db._store_sensitive_record_integrity(conn, "audit_chain_heads", record_id, payload)
    write_audit_anchor(sequence, event_hash, event_id=event_id)


def invalidate_audit_chain_head() -> None:
    with db._AUDIT_CACHE_LOCK:
        db._AUDIT_CHAIN_HEADS.pop(str(db.db_path()), None)


def latest_persisted_audit_chain_head(conn: sqlite3.Connection) -> dict[str, Any] | None:
    try:
        row = conn.execute(
            """
            SELECT id, sequence, event_hash, event_id, created_at
            FROM audit_chain_heads
            ORDER BY sequence DESC, created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    sequence = int(row["sequence"] or 0)
    event_hash = str(row["event_hash"] or "")
    if sequence <= 0 or not event_hash:
        return None
    db._require_sensitive_record_integrity(
        conn,
        "audit_chain_heads",
        str(row["id"]),
        audit_chain_head_integrity_payload(
            record_id=str(row["id"]),
            sequence=sequence,
            event_hash=event_hash,
            event_id=str(row["event_id"] or ""),
            created_at=str(row["created_at"] or ""),
        ),
    )
    return {"sequence": sequence, "event_hash": event_hash, "event_id": str(row["event_id"] or "")}


def audit_chain_head_integrity_payload(
    *,
    record_id: str,
    sequence: int,
    event_hash: str,
    event_id: str,
    created_at: str,
) -> str:
    return json.dumps(
        {
            "id": record_id,
            "sequence": int(sequence),
            "event_hash": event_hash,
            "event_id": event_id,
            "created_at": created_at,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def audit_anchor_path() -> Path:
    return Path(get_base_settings().data_dir) / db.AUDIT_ANCHOR_FILE


def write_audit_anchor(sequence: int, event_hash: str, *, event_id: str = "") -> None:
    payload = {
        "schema": 1,
        "sequence": int(sequence),
        "event_hash": str(event_hash),
        "event_id": str(event_id or ""),
        "updated_at": db._now_iso(),
    }
    payload["anchor_sha256"] = sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path = audit_anchor_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_audit_anchor() -> dict[str, Any] | None:
    path = audit_anchor_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sequence": 0, "event_hash": "", "event_id": "", "invalid": True}
    if not isinstance(payload, dict):
        return {"sequence": 0, "event_hash": "", "event_id": "", "invalid": True}
    expected_payload = {key: value for key, value in payload.items() if key != "anchor_sha256"}
    expected_checksum = sha256(
        json.dumps(expected_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    actual_checksum = str(payload.get("anchor_sha256") or "")
    if not actual_checksum or not hmac.compare_digest(actual_checksum, expected_checksum):
        return {"sequence": 0, "event_hash": "", "event_id": "", "invalid": True}
    return payload


def audit_event_hash(event: dict[str, Any]) -> str:
    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def audit_event_hmac(event_hash: str, *, secret: str | None = None) -> str:
    key = secret if secret is not None else audit_hmac_secret()
    return hmac.new(key.encode("utf-8"), event_hash.encode("utf-8"), sha256).hexdigest()


def audit_hmac_secret() -> str:
    from app.security.local_secret import load_or_create_local_secret

    configured = str(get_env("LENGRVIS_AUDIT_HMAC_SECRET") or "").strip()
    if configured:
        return configured

    secret_path = active_audit_hmac_secret_path()
    key = str(secret_path)
    with db._AUDIT_CACHE_LOCK:
        cached = db._AUDIT_SECRET_CACHE.get(key)
    if cached:
        return cached

    secret = load_or_create_local_secret(
        secret_path,
        unavailable_message="Audit HMAC secret is unavailable.",
    )
    with db._AUDIT_CACHE_LOCK:
        db._AUDIT_SECRET_CACHE[key] = secret
    return secret


def active_audit_hmac_secret_path() -> Path:
    secret_path = db.audit_hmac_secret_path()
    legacy_path = db.db_path().parent / db.AUDIT_HMAC_SECRET_FILE
    if secret_path == legacy_path or secret_path.exists() or not legacy_path.exists():
        return secret_path
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_path.replace(secret_path)
        return secret_path
    except OSError:
        return legacy_path
