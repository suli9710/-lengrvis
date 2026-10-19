"""Recall actual matching chunks before candidate budgets and scope filtering."""

from __future__ import annotations

import pytest

from app.core import db
from app.indexer.fts_index import FTSIndex
from app.indexer.fts_query import fts_match_query
from app.indexer.vector_index import VectorIndex

MARKER = "azimuthreceipt"


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))


def embeddings(texts):
    return [[1.0, 0.0] if MARKER in text else [0.0, 1.0] for text in texts]


def long_document(path):
    path.write_text(
        "\n\n".join([("Routine introduction " * 60)[:1100] for _ in range(100)] + [f"{MARKER}: unique tail answer."]),
        encoding="utf-8",
    )


@pytest.mark.parametrize("missing_embeddings", [False, True])
@pytest.mark.parametrize("force_like", [False, True])
def test_tail_keyword_survives_small_candidate_budget(tmp_path, monkeypatch, missing_embeddings, force_like):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    long_document(workspace / "handbook.txt")
    index = FTSIndex(embedder=embeddings)
    index.rebuild([str(workspace)])
    if missing_embeddings:
        with db.connect() as conn:
            conn.execute("DELETE FROM document_chunk_embeddings")
    if force_like:
        # Force the production LIKE fallback without rebuilding FTS at init.
        monkeypatch.setattr("app.indexer.vector_index.fts_match_query", lambda _: '"unterminated')
    vector = VectorIndex(embedder=embeddings)
    candidates = vector._lexical_chunks(MARKER, 1)
    assert len(candidates) == 1 and candidates[0]["chunk_index"] >= 99 and MARKER in candidates[0]["text"]
    result = vector.search(MARKER, candidate_limit=1, allowed_directories=[str(workspace)])
    assert result["results"][0]["chunk_id"] == candidates[0]["chunk_id"]
    assert MARKER in result["results"][0]["snippet"]
    assert result["source"] == ("fts_lexical_fallback" if missing_embeddings else "fts_vector_rerank")


def test_multiple_files_compete_by_hit_rank_instead_of_leading_chunks(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    long_document(workspace / "a-handbook.txt")
    (workspace / "z-receipt.txt").write_text(f"{MARKER} " * 12, encoding="utf-8")
    FTSIndex(embedder=embeddings).rebuild([str(workspace)])
    with db.connect() as conn:
        hits = conn.execute(
            "SELECT chunk_id, bm25(document_chunks_fts) AS rank FROM document_chunks_fts WHERE document_chunks_fts MATCH ? ORDER BY rank",
            (fts_match_query(MARKER),),
        ).fetchall()
    vector = VectorIndex(embedder=embeddings)
    candidates = vector._candidate_chunks(MARKER, 2)
    assert [row["chunk_id"] for row in candidates] == [row["chunk_id"] for row in hits[:2]]
    assert len({row["file_id"] for row in candidates}) == 2
    assert all(MARKER in row["text"] for row in candidates)
    first = vector.search(MARKER, candidate_limit=1, allowed_directories=[str(workspace)])
    assert first["results"][0]["chunk_id"] == hits[0]["chunk_id"]
    both = vector.search(MARKER, candidate_limit=2, limit=2, allowed_directories=[str(workspace)])
    assert len(both["results"]) == 2


def test_text_only_hit_competes_with_embedded_hits_in_the_same_budget(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a-embedded.txt").write_text(f"A note about {MARKER} among other things.", encoding="utf-8")
    (workspace / "z-text-only.txt").write_text(f"{MARKER} " * 20, encoding="utf-8")
    FTSIndex(embedder=embeddings).rebuild([str(workspace)])
    with db.connect() as conn:
        conn.execute(
            "DELETE FROM document_chunk_embeddings WHERE file_id IN (SELECT id FROM indexed_files WHERE name = ?)",
            ("z-text-only.txt",),
        )
    result = VectorIndex(embedder=embeddings).search(MARKER, candidate_limit=1, allowed_directories=[str(workspace)])
    assert result["candidate_count"] == 1 and result["count"] == 1
    assert result["source"] == "fts_lexical_fallback"
    assert result["results"][0]["name"] == "z-text-only.txt"


@pytest.mark.parametrize("missing_embeddings", [False, True])
def test_unauthorized_high_rank_hits_do_not_consume_candidate_budget(tmp_path, missing_embeddings):
    allowed = tmp_path / "allowed"
    revoked = tmp_path / "revoked"
    allowed.mkdir()
    revoked.mkdir()
    (allowed / "receipt.txt").write_text(f"A permitted {MARKER} receipt.", encoding="utf-8")
    for index in range(5):
        (revoked / f"secret-{index}.txt").write_text(f"{MARKER} " * 20, encoding="utf-8")
    FTSIndex(embedder=embeddings).rebuild([str(allowed), str(revoked)])
    if missing_embeddings:
        with db.connect() as conn:
            conn.execute("DELETE FROM document_chunk_embeddings")
    result = VectorIndex(embedder=embeddings).search(MARKER, candidate_limit=1, allowed_directories=[str(allowed)])
    assert result["candidate_count"] == 1 and result["results"][0]["name"] == "receipt.txt"
    assert str(revoked) not in str(result)


def test_existing_fts_schema_migrates_chunk_ids_without_reembedding(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    long_document(workspace / "handbook.txt")
    FTSIndex(embedder=embeddings).rebuild([str(workspace)])
    with db.connect() as conn:
        before = [
            tuple(row)
            for row in conn.execute("SELECT id, chunk_id, embedding FROM document_chunk_embeddings ORDER BY id")
        ]
        conn.execute("DROP TABLE document_chunks_fts")
        conn.execute("CREATE VIRTUAL TABLE document_chunks_fts USING fts5(file_id, path, text, tokenize='trigram')")
        conn.execute(
            "INSERT INTO document_chunks_fts (file_id, path, text) SELECT dc.file_id, f.normalized_path, dc.text FROM document_chunks dc JOIN indexed_files f ON f.id = dc.file_id"
        )
    db.init_db(force=True)
    with db.connect() as conn:
        after = [
            tuple(row)
            for row in conn.execute("SELECT id, chunk_id, embedding FROM document_chunk_embeddings ORDER BY id")
        ]
        missing_ids = conn.execute(
            "SELECT COUNT(*) FROM document_chunks_fts WHERE chunk_id IS NULL OR chunk_id = ''"
        ).fetchone()[0]
    assert after == before and missing_ids == 0
    result = VectorIndex(embedder=embeddings).search(MARKER, candidate_limit=1, allowed_directories=[str(workspace)])
    assert result["results"][0]["chunk_index"] >= 99
