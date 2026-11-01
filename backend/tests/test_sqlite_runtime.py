from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.core import sqlite_runtime


@pytest.mark.parametrize(
    ("version", "fixed"),
    [
        ("3.49.1", False),
        ("3.51.2", False),
        ("3.51.3", True),
        ("3.44.6", True),
        ("3.50.6", False),
        ("3.50.7", True),
        ("3.53.1", True),
        ("3.51.3-custom", False),
    ],
)
def test_wal_runtime_version_policy(version, fixed):
    assert sqlite_runtime.wal_reset_fixed(version) is fixed


def test_runtime_probe_has_no_user_data_side_effects(tmp_path):
    entry = Path(__file__).resolve().parents[1] / "main.py"
    result = subprocess.run(
        [sys.executable, str(entry), "--sqlite-runtime-info"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    evidence = json.loads(result.stdout)
    assert evidence["sqlite_version"] == sqlite_runtime.sqlite3.sqlite_version
    assert evidence["sqlite_source_id"]
    assert result.returncode == (0 if evidence["wal_reset_fixed"] else 1)
    assert list(tmp_path.iterdir()) == []


def test_unsafe_runtime_is_rejected_before_release_database_creation(monkeypatch, tmp_path):
    from app.core import db

    monkeypatch.setenv("LENGRVIS_RELEASE_BUILD", "true")
    monkeypatch.setattr(sqlite_runtime.sqlite3, "sqlite_version", "3.49.1")
    with pytest.raises(RuntimeError, match="WAL-reset"):
        db._open_connection(tmp_path / "not-created.db")
    assert not (tmp_path / "not-created.db").exists()


def test_old_development_sqlite_uses_rollback_journal(monkeypatch, tmp_path):
    from app.core import db
    from app.security import execution_isolation

    monkeypatch.setattr(execution_isolation, "release_profile_active", lambda: False)
    monkeypatch.setattr(sqlite_runtime.sqlite3, "sqlite_version", "3.49.1")
    conn = db._open_connection(tmp_path / "development.db")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()


@pytest.mark.parametrize("changed", [False, True])
def test_frozen_probe_is_disposable_and_bound_to_executed_bytes(monkeypatch, tmp_path, changed):
    import build_backend
    from app.core import process_tree

    candidate = tmp_path / "backend.exe"
    candidate.write_bytes(b"candidate-before")
    monkeypatch.setenv("LENGRVIS_FULL_BACKEND", "1")
    monkeypatch.setenv("LENGRVIS_API_KEY", "do-not-inherit")

    def run(command, **kwargs):
        assert command == [str(candidate), "--sqlite-runtime-info"]
        assert kwargs["cwd"] != Path.cwd()
        assert kwargs["env"]["LENGRVIS_FULL_BACKEND"] == "0"
        assert kwargs["env"]["LENGRVIS_BACKEND_PORT"] == "0"
        assert kwargs["env"]["LENGRVIS_DATA_DIR"].startswith(str(kwargs["cwd"]))
        assert "LENGRVIS_API_KEY" not in kwargs["env"]
        assert kwargs["hide_window"] is True
        if changed:
            candidate.write_bytes(b"candidate-after")
        evidence = {
            "schema": "lengrvis-sqlite-runtime/v1",
            "frozen": True,
            "sqlite_version": "3.53.1",
            "wal_reset_fixed": True,
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(evidence))

    monkeypatch.setattr(process_tree, "run_process_tree", run)
    if changed:
        with pytest.raises(RuntimeError, match="changed during"):
            build_backend.verify_frozen_sqlite(candidate, tmp_path)
        assert not (tmp_path / "backend-sqlite-runtime.json").exists()
    else:
        evidence_path = build_backend.verify_frozen_sqlite(candidate, tmp_path)
        assert json.loads(evidence_path.read_text())["artifact_sha256"]
