from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.acceleration import onnx_sessions
from app.core import db
from app.indexer.fts_index import FTSIndex
from app.llm.registry import invalidate_settings_cache


def test_db_connect_reuses_thread_connection_for_same_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.close_thread_connection()
    try:
        with db.connect() as first:
            with db.connect() as second:
                assert second is first
    finally:
        db.close_thread_connection()


def test_reset_init_db_cache_closes_reused_connection(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    db.close_thread_connection()
    try:
        with db.connect() as first:
            first.execute("SELECT 1")

        db.reset_init_db_cache()

        with pytest.raises(sqlite3.ProgrammingError):
            first.execute("SELECT 1")
        with db.connect() as second:
            assert second is not first
    finally:
        db.close_thread_connection()


def test_onnx_session_cache_evicts_least_recent(monkeypatch, tmp_path: Path) -> None:
    class FakeOrt:
        created: list[str] = []

        class InferenceSession:
            def __init__(self, model_path: str, providers: list[object]) -> None:
                FakeOrt.created.append(model_path)
                self.model_path = model_path
                self.providers = providers

    monkeypatch.setenv("LENGRVIS_ONNX_SESSION_CACHE_MAX_ENTRIES", "2")
    monkeypatch.setattr(onnx_sessions, "import_onnxruntime", lambda: FakeOrt)
    onnx_sessions.clear_session_cache()
    try:
        backends = [
            onnx_sessions.OnnxSessionBackend(
                kind="onnx",
                model_path=str(tmp_path / f"model-{index}.onnx"),
                execution_provider=onnx_sessions.CPU_PROVIDER,
                available_providers=[onnx_sessions.CPU_PROVIDER],
            )
            for index in range(3)
        ]

        first = onnx_sessions.create_inference_session(backends[0])
        second = onnx_sessions.create_inference_session(backends[1])
        assert onnx_sessions.create_inference_session(backends[0]) is first

        third = onnx_sessions.create_inference_session(backends[2])

        assert third is not second
        assert onnx_sessions.create_inference_session(backends[1]) is not second
        assert FakeOrt.created == [
            backends[0].model_path,
            backends[1].model_path,
            backends[2].model_path,
            backends[1].model_path,
        ]
    finally:
        onnx_sessions.clear_session_cache()


def test_index_rebuild_limit_preserves_existing_index(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(data_dir))
    db.close_thread_connection()
    db.reset_init_db_cache()
    invalidate_settings_cache()
    db.init_db()

    def embedder(texts: list[str]) -> list[list[float]]:
        return [[1.0] for _text in texts]

    (workspace / "kept.txt").write_text("keep this indexed document", encoding="utf-8")
    first = FTSIndex(embedder=embedder).rebuild([str(workspace)])
    assert first["files_indexed"] == 1

    (workspace / "too-many.txt").write_text("this file exceeds the configured rebuild cap", encoding="utf-8")
    monkeypatch.setenv("LENGRVIS_INDEX_REBUILD_MAX_FILES", "1")
    invalidate_settings_cache()
    second = FTSIndex(embedder=embedder).rebuild([str(workspace)])

    assert second["aborted"] is True
    assert "file limit" in second["abort_reason"]
    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM indexed_files ORDER BY name").fetchall()
    assert [row["name"] for row in rows] == ["kept.txt"]

    db.close_thread_connection()
