"""Encrypted, bounded SQLite snapshots with non-destructive recovery checks.

Plaintext stays in memory. Recovery deliberately opens a read-only in-memory
database: restoring old approvals or replacing a running database is not safe.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.local_data_lifecycle import tracks_local_data
from app.security.sensitive_data_crypto import decrypt_sensitive_bytes, encrypt_sensitive_bytes, sensitive_data_key_path
from app.tools.filesystem_safety import ensure_mutation_path_safe, safe_create_bytes, safe_move_file

BACKUP_DIR = "database-backups"
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
KEEP_SNAPSHOTS = 3
_NAME = re.compile(r"snapshot-\d{8}T\d{12}Z-[0-9a-f]{32}\.db\.enc")


def _root(data_dir: Path) -> Path:
    root = data_dir / BACKUP_DIR
    ensure_mutation_path_safe(root, [str(data_dir)], include_self=True)
    return root


def _binding(name: str) -> dict[str, str]:
    return {"database": "lengrvis.db", "snapshot": name}


def _erase_revision(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT data FROM erase_state WHERE id = 1").fetchone()
    if row is None:
        return None
    if json.loads(row[0]).get("status") != "complete":
        raise RuntimeError("Database backup is blocked while local data erasure is pending.")
    return str(row[0])


@contextmanager
def _erase_connection(data_dir: Path) -> Iterator[sqlite3.Connection]:
    path = data_dir / "privacy-erase-state.sqlite3"
    ensure_mutation_path_safe(path, [str(data_dir)], include_self=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, isolation_level=None, timeout=5.0)) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS erase_state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        yield conn


@tracks_local_data
def create_database_snapshot(conn: sqlite3.Connection, data_dir: Path) -> Path:
    """Use Online Backup so committed pages still in WAL are included."""
    if conn.in_transaction:
        raise RuntimeError("Database snapshots require a connection outside a transaction.")
    data_dir = data_dir.resolve()
    with _erase_connection(data_dir) as erasure:
        erase_revision = _erase_revision(erasure)
    root = _root(data_dir)
    deadline = time.monotonic() + 30
    page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])

    def progress(status: int, remaining: int, total: int) -> None:
        if total * page_size > MAX_SNAPSHOT_BYTES:
            raise RuntimeError("Database exceeds the encrypted snapshot memory limit.")
        if time.monotonic() > deadline:
            raise TimeoutError("Database snapshot could not finish within 30 seconds.")

    if int(conn.execute("PRAGMA page_count").fetchone()[0]) * page_size > MAX_SNAPSHOT_BYTES:
        raise RuntimeError("Database exceeds the encrypted snapshot memory limit.")
    with closing(sqlite3.connect(":memory:")) as snapshot:
        conn.backup(snapshot, pages=256, progress=progress, sleep=0.01)
        payload = snapshot.serialize()
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise RuntimeError("Database exceeds the encrypted snapshot memory limit.")
    # SQLite documents this header normalization for deserializing a complete
    # WAL image: https://www.sqlite.org/c3ref/deserialize.html
    payload = payload[:18] + b"\x01\x01" + payload[20:]
    with closing(sqlite3.connect(":memory:")) as check:
        check.deserialize(payload)
        _require_integrity(check)
    name = f"snapshot-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{uuid.uuid4().hex}.db.enc"
    encrypted = encrypt_sensitive_bytes(payload, purpose="database-snapshot", binding=_binding(name), data_dir=data_dir)
    target = root / name
    # A manual backup runs in another process, outside local_data_lock. Bind
    # publication to the durable erase revision and briefly lock that state DB
    # so an erase cannot race between validation and the ciphertext fsync.
    with _erase_connection(data_dir) as erasure:
        erasure.execute("BEGIN IMMEDIATE")
        try:
            if _erase_revision(erasure) != erase_revision:
                raise RuntimeError("Local data was erased during the backup; no snapshot was published.")
            staging = root / (name + ".pending")
            safe_create_bytes(staging, encrypted, [str(data_dir)])
            safe_move_file(staging, target, [str(data_dir)])
            # Never remove unrelated files or follow reparse points.
            snapshots = sorted(
                (p for p in root.iterdir() if _NAME.fullmatch(p.name)), key=lambda p: p.name, reverse=True
            )
            for old in snapshots[KEEP_SNAPSHOTS:]:
                ensure_mutation_path_safe(old, [str(data_dir)], include_self=True)
                old.unlink()
        finally:
            erasure.rollback()
    return target


@contextmanager
def recovered_snapshot(data_dir: Path, name: str) -> Iterator[sqlite3.Connection]:
    """Recover for inspection only; never reactivate persisted permissions."""
    if not _NAME.fullmatch(name):
        raise ValueError("Invalid database snapshot name.")
    data_dir = data_dir.resolve()
    path = _root(data_dir) / name
    ensure_mutation_path_safe(path, [str(data_dir)], include_self=True)
    if not sensitive_data_key_path(data_dir).is_file():
        raise RuntimeError("The original installation encryption key is required for recovery.")
    with path.open("rb") as source:
        envelope = source.read(MAX_SNAPSHOT_BYTES + 1025)
    if len(envelope) > MAX_SNAPSHOT_BYTES + 1024:
        raise ValueError("Encrypted database snapshot exceeds the size limit.")
    payload = decrypt_sensitive_bytes(envelope, purpose="database-snapshot", binding=_binding(name), data_dir=data_dir)
    with closing(sqlite3.connect(":memory:")) as restored:
        restored.deserialize(payload)
        restored.execute("PRAGMA query_only = ON")
        _require_integrity(restored)
        yield restored


def verify_database_snapshot(data_dir: Path, name: str) -> dict[str, Any]:
    with recovered_snapshot(data_dir, name) as conn:
        counts = {}
        for table in ("tasks", "plans", "approvals", "audit_events"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                counts[table] = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]  # noqa: S608
        return {"integrity": "ok", "snapshot": name, "row_counts": counts, "approval_reactivation_allowed": False}


def _require_integrity(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise RuntimeError("Database snapshot failed SQLite integrity verification.")


def snapshot_before_schema_upgrade(conn: sqlite3.Connection) -> None:
    from app.core.db_migrations import MIGRATIONS, validate_schema_compatibility

    applied = validate_schema_compatibility(conn)
    if all(migration.version in applied for migration in MIGRATIONS):
        return
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
        return  # A new database has no user data to protect.
    database_path = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    if database_path:
        create_database_snapshot(conn, Path(database_path).parent)
