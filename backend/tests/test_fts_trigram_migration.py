from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import db
from app.indexer.fts_index import FTSIndex
from app.indexer.fts_query import fts_match_query


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))


@pytest.mark.skip(reason="Requires init_db(force=) and trigram FTS migration in db.py (batch-3 WIP)")
def test_fts_trigram_migration_repopulates_existing_chunks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("automobile repair manual and engine maintenance records", encoding="utf-8")

    FTSIndex().rebuild([str(workspace)])

    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS document_chunks_fts")
        conn.execute("CREATE VIRTUAL TABLE document_chunks_fts USING fts5(file_id, path, text)")
        conn.execute("DELETE FROM document_chunks_fts")
        for row in conn.execute(
            "SELECT dc.file_id, dc.text, f.data FROM document_chunks dc JOIN indexed_files f ON f.id = dc.file_id"
        ).fetchall():
            file_data = json.loads(row["data"])
            conn.execute(
                "INSERT INTO document_chunks_fts (file_id, path, text) VALUES (?, ?, ?)",
                (row["file_id"], file_data["path"], row["text"]),
            )

    db.init_db(force=True)

    with db.connect() as conn:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='document_chunks_fts'"
        ).fetchone()["sql"]
        count = conn.execute("SELECT COUNT(*) AS count FROM document_chunks_fts").fetchone()["count"]
        rows = conn.execute(
            "SELECT file_id FROM document_chunks_fts WHERE document_chunks_fts MATCH ?",
            (fts_match_query("automobile"),),
        ).fetchall()

    assert "trigram" in str(ddl).lower()
    assert count >= 1
    assert rows


@pytest.mark.skip(reason="Requires cached init_db FTS drift rebuild in db.py (batch-3 WIP)")
def test_fts_drift_rebuild_on_cached_init_db(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("automobile repair manual", encoding="utf-8")

    FTSIndex().rebuild([str(workspace)])
    db.init_db()

    with db.connect() as conn:
        conn.execute("DELETE FROM document_chunks_fts")

    db.init_db()

    with db.connect() as conn:
        chunk_count = conn.execute("SELECT COUNT(*) AS count FROM document_chunks").fetchone()["count"]
        fts_count = conn.execute("SELECT COUNT(*) AS count FROM document_chunks_fts").fetchone()["count"]

    assert chunk_count >= 1
    assert fts_count >= chunk_count


def test_fts_search_short_cjk_query_falls_back_to_like(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("汽车维修手册与发动机保养记录", encoding="utf-8")

    FTSIndex().rebuild([str(workspace)])
    db.init_db()

    results = FTSIndex().search("汽车", limit=5, allowed_directories=[str(workspace)])

    assert results
    assert any("汽车" in str(item.get("snippet") or "") for item in results)
