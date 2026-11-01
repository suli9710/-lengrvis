from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from app.core import db, db_backups
from app.security.sensitive_data_crypto import SensitiveDataDecryptionError
from app.services.privacy_erase_service import _erase_managed_files


def test_snapshot_recovers_committed_wal_without_plaintext_or_reactivating_approvals(tmp_path):
    with db.using_data_dir(tmp_path), closing(sqlite3.connect(tmp_path / "lengrvis.db")) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute("CREATE TABLE tasks (content TEXT)")
        source.execute("INSERT INTO tasks VALUES ('PRIVATE_SNAPSHOT_CONTENT')")
        source.commit()
        snapshot = db_backups.create_database_snapshot(source, tmp_path)
        assert b"PRIVATE_SNAPSHOT_CONTENT" not in snapshot.read_bytes()
        assert b"SQLite format" not in snapshot.read_bytes()
        with db_backups.recovered_snapshot(tmp_path, snapshot.name) as restored:
            assert restored.execute("SELECT content FROM tasks").fetchone()[0] == "PRIVATE_SNAPSHOT_CONTENT"
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                restored.execute("DELETE FROM tasks")
        report = db_backups.verify_database_snapshot(tmp_path, snapshot.name)
        assert report["row_counts"] == {"tasks": 1}
        assert report["approval_reactivation_allowed"] is False
        assert source.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1


def test_snapshot_tamper_and_path_escape_are_rejected(tmp_path):
    with db.using_data_dir(tmp_path), closing(sqlite3.connect(":memory:")) as source:
        source.execute("CREATE TABLE sample (value)")
        snapshot = db_backups.create_database_snapshot(source, tmp_path)
        original = snapshot.read_bytes()
        snapshot.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        with pytest.raises(SensitiveDataDecryptionError):
            db_backups.verify_database_snapshot(tmp_path, snapshot.name)
        with pytest.raises(ValueError, match="snapshot name"):
            db_backups.verify_database_snapshot(tmp_path, "../outside.db.enc")


def test_snapshot_retention_and_privacy_erase_include_database_backups(tmp_path):
    with db.using_data_dir(tmp_path), closing(sqlite3.connect(":memory:")) as source:
        source.execute("CREATE TABLE sample (value)")
        for _ in range(db_backups.KEEP_SNAPSHOTS + 1):
            db_backups.create_database_snapshot(source, tmp_path)
        root = tmp_path / db_backups.BACKUP_DIR
        assert len(list(root.iterdir())) == db_backups.KEEP_SNAPSHOTS
        counts = _erase_managed_files(tmp_path)
        assert counts["database_backups"] == db_backups.KEEP_SNAPSHOTS
        assert not root.exists()


def test_migration_backup_failure_preserves_original_schema(monkeypatch, tmp_path):
    with db.using_data_dir(tmp_path):
        db.reset_connection_state()
        with db.connect() as conn:
            conn.execute("CREATE TABLE original_user_data (value)")
            conn.execute("INSERT INTO original_user_data VALUES ('preserve')")

        def unavailable(*args, **kwargs):
            raise RuntimeError("snapshot unavailable")

        monkeypatch.setattr(db_backups, "create_database_snapshot", unavailable)
        with pytest.raises(RuntimeError, match="snapshot unavailable"):
            db._init_db_schema()
        with db.connect() as conn:
            assert conn.execute("SELECT value FROM original_user_data").fetchone()[0] == "preserve"
            assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
        db.reset_connection_state()


def test_backup_cannot_republish_content_after_another_process_erases(monkeypatch, tmp_path):
    import json

    encrypt = db_backups.encrypt_sensitive_bytes

    def encrypt_then_erase(*args, **kwargs):
        encrypted = encrypt(*args, **kwargs)
        # Simulate the persisted state from a completed erase in another process.
        with sqlite3.connect(tmp_path / "privacy-erase-state.sqlite3") as state:
            state.execute(
                "INSERT OR REPLACE INTO erase_state VALUES (1, ?)",
                (json.dumps({"status": "complete", "operation_id": "new-erase"}),),
            )
        return encrypted

    monkeypatch.setattr(db_backups, "encrypt_sensitive_bytes", encrypt_then_erase)
    with db.using_data_dir(tmp_path), closing(sqlite3.connect(":memory:")) as source:
        source.execute("CREATE TABLE sample (value)")
        with pytest.raises(RuntimeError, match="erased during"):
            db_backups.create_database_snapshot(source, tmp_path)
        assert not list((tmp_path / db_backups.BACKUP_DIR).glob("*.db.enc"))
