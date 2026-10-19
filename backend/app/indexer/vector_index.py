from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from app.config import AppSettings
from app.core import db
from app.indexer.embedding_service import Embedder, embed_texts_sync
from app.indexer.embedding_storage import cosine_similarity_batch, vector_from_storage
from app.indexer.fts_query import fts_match_query

logger = logging.getLogger(__name__)


DEFAULT_LIMIT = 10
DEFAULT_CANDIDATE_LIMIT = 80
DEFAULT_SCAN_LIMIT = 1000


class VectorIndex:
    def __init__(self, *, embedder: Embedder | None = None) -> None:
        self.embedder = embedder

    def search(
        self,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
        scan_limit: int = DEFAULT_SCAN_LIMIT,
        allowed_directories: list[str] | None = None,
        settings: AppSettings | None = None,
    ) -> dict[str, Any]:
        db.init_db()
        query = str(query or "").strip()
        if not query:
            return {"query": query, "results": [], "count": 0, "candidate_count": 0, "source": "vector"}
        allowed_bases = _allowed_bases(allowed_directories)
        if not allowed_bases:
            return {"query": query, "results": [], "count": 0, "candidate_count": 0, "source": "vector"}

        query_vector = embed_texts_sync([query], embedder=self.embedder, settings=settings)[0]
        candidates = self._candidate_chunks(query, candidate_limit, allowed_bases=allowed_bases)
        # Embedded and text-only hits share one relevance-ordered budget.
        missing_embedding_candidates = [
            row
            for row in candidates
            if (stored_vector := vector_from_storage(row.get("embedding"))) is None or stored_vector.size == 0
        ]
        source = "fts_vector_rerank"
        if candidates and len(missing_embedding_candidates) == len(candidates):
            fallback_results = _collapse_by_file(_lexical_fallback_rows(candidates, query), limit)
            return {
                "query": query,
                "results": fallback_results,
                "count": len(fallback_results),
                "candidate_count": len(candidates),
                "source": "fts_lexical_fallback",
            }
        if not candidates:
            candidates = self._recent_chunks(scan_limit, allowed_bases=allowed_bases)
            source = "vector_scan"

        ranked = _rank_vector_candidates(candidates, query_vector, query)

        if missing_embedding_candidates:
            ranked.extend(_lexical_fallback_rows(missing_embedding_candidates, query))
            source = "fts_mixed_fallback"

        ranked.sort(key=lambda item: (-item["score"], item["path"], item["chunk_index"]))
        collapsed = _collapse_by_file(ranked, limit)
        return {
            "query": query,
            "results": collapsed,
            "count": len(collapsed),
            "candidate_count": len(candidates),
            "source": source,
        }

    def _candidate_chunks(
        self, query: str, limit: int, *, allowed_bases: list[Path] | None = None
    ) -> list[dict[str, Any]]:
        return self._matching_chunks(query, limit, allowed_bases=allowed_bases)

    def _lexical_chunks(
        self, query: str, limit: int, *, allowed_bases: list[Path] | None = None
    ) -> list[dict[str, Any]]:
        return self._matching_chunks(query, limit, allowed_bases=allowed_bases)

    def _matching_chunks(self, query: str, limit: int, *, allowed_bases: list[Path] | None) -> list[dict[str, Any]]:
        columns = (
            "dc.file_id, dc.id AS chunk_id, dc.chunk_index, dc.text, f.name, f.normalized_path AS path, e.embedding"
        )
        joins = """
            JOIN indexed_files f ON f.id = dc.file_id
            LEFT JOIN document_chunk_embeddings e ON e.chunk_id = dc.id
        """
        with db.connect() as conn:
            _register_path_filter(conn, allowed_bases)
            try:
                rows = conn.execute(
                    f"""
                    SELECT {columns}, bm25(document_chunks_fts) AS lexical_rank
                    FROM document_chunks_fts
                    JOIN document_chunks dc ON dc.id = document_chunks_fts.chunk_id
                    {joins}
                    WHERE document_chunks_fts MATCH ? AND vector_path_allowed(f.normalized_path)
                    ORDER BY lexical_rank, f.normalized_path, dc.chunk_index, dc.id
                    LIMIT ?
                    """,  # noqa: S608 - column and join fragments are internal constants.
                    (fts_match_query(query), max(1, limit)),
                ).fetchall()
            except sqlite3.Error as exc:
                logger.debug("FTS chunk lookup failed, using LIKE fallback: %s", exc)
                rows = []
            if rows:
                candidates = []
                for row in rows:
                    item = dict(row)
                    magnitude = abs(float(item["lexical_rank"] or 0.0))
                    item["lexical_score"] = magnitude / (1.0 + magnitude)
                    candidates.append(item)
                return candidates
            rows = conn.execute(
                f"""
                SELECT {columns}, 1.0 AS lexical_score
                FROM document_chunks dc
                {joins}
                WHERE dc.text LIKE ? AND vector_path_allowed(f.normalized_path)
                ORDER BY f.normalized_path, dc.chunk_index, dc.id
                LIMIT ?
                """,  # noqa: S608 - column and join fragments are internal constants.
                (f"%{query}%", max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def _recent_chunks(self, limit: int, *, allowed_bases: list[Path] | None = None) -> list[dict[str, Any]]:
        with db.connect() as conn:
            _register_path_filter(conn, allowed_bases)
            rows = conn.execute(
                """
                SELECT
                    dc.file_id,
                    dc.id AS chunk_id,
                    dc.chunk_index,
                    dc.text,
                    f.name,
                    f.normalized_path AS path,
                    e.embedding,
                    0.0 AS lexical_score
                FROM document_chunks dc
                JOIN indexed_files f ON f.id = dc.file_id
                JOIN document_chunk_embeddings e ON e.chunk_id = dc.id
                WHERE vector_path_allowed(f.normalized_path)
                ORDER BY f.indexed_at DESC, dc.chunk_index ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def _allowed_bases(allowed_directories: list[str] | None) -> list[Path]:
    if allowed_directories is None:
        return []
    bases: list[Path] = []
    for raw_base in allowed_directories:
        try:
            bases.append(Path(raw_base).expanduser().resolve(strict=False))
        except OSError:
            continue
    return bases


def _register_path_filter(conn: sqlite3.Connection, allowed_bases: list[Path] | None) -> None:
    conn.create_function(
        "vector_path_allowed",
        1,
        lambda path: allowed_bases is None or _within_allowed_bases(str(path or ""), allowed_bases),
    )


def _within_allowed_bases(path: str, allowed_bases: list[Path]) -> bool:
    if not path or not allowed_bases:
        return False
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except OSError:
        return False
    for base in allowed_bases:
        if resolved == base or base in resolved.parents:
            return True
    return False


def _rank_vector_candidates(
    candidates: list[dict[str, Any]],
    query_vector: list[float],
    query: str,
) -> list[dict[str, Any]]:
    query_dim = len(query_vector)
    vectors: list[np.ndarray] = []
    valid_rows: list[dict[str, Any]] = []
    ranked: list[dict[str, Any]] = []
    for row in candidates:
        vector = vector_from_storage(row.get("embedding"))
        if vector is None or vector.size == 0:
            continue
        lexical_score = float(row.get("lexical_score") or 0.0)
        if vector.size != query_dim:
            ranked.append(
                _ranked_candidate_row(
                    row,
                    query=query,
                    score=min(0.2, lexical_score / 100.0),
                    vector_score=0.0,
                    lexical_score=lexical_score,
                )
            )
            continue
        vectors.append(vector)
        valid_rows.append(row)

    if valid_rows:
        matrix = np.vstack(vectors)
        scores = cosine_similarity_batch(query_vector, matrix)
        for index, row in enumerate(valid_rows):
            vector_score = float(scores[index])
            lexical_score = float(row.get("lexical_score") or 0.0)
            ranked.append(
                _ranked_candidate_row(
                    row,
                    query=query,
                    score=vector_score + min(0.2, lexical_score / 100.0),
                    vector_score=vector_score,
                    lexical_score=lexical_score,
                )
            )
    return ranked


def _ranked_candidate_row(
    row: dict[str, Any],
    *,
    query: str,
    score: float,
    vector_score: float,
    lexical_score: float,
) -> dict[str, Any]:
    return {
        "file_id": row["file_id"],
        "chunk_id": row["chunk_id"],
        "chunk_index": row["chunk_index"],
        "path": row["path"],
        "name": row["name"],
        "snippet": _snippet(row["text"], query),
        "score": score,
        "vector_score": vector_score,
        "lexical_score": lexical_score,
    }


def _snippet(text: str, query: str, *, size: int = 240) -> str:
    lowered = text.lower()
    tokens = [token.lower() for token in query.split() if token]
    start = 0
    for token in tokens:
        found = lowered.find(token)
        if found >= 0:
            start = max(0, found - 80)
            break
    snippet = text[start : start + size].replace("\n", " ").strip()
    return snippet


def _collapse_by_file(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        current = best.get(row["file_id"])
        if current is None or row["score"] > current["score"]:
            best[row["file_id"]] = row
    collapsed = sorted(best.values(), key=lambda item: (-item["score"], item["path"]))
    return collapsed[: max(1, limit)]


def _lexical_fallback_rows(rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    ranked = []
    for row in rows:
        lexical_score = float(row.get("lexical_score") or 0.0)
        ranked.append(
            {
                "file_id": row["file_id"],
                "chunk_id": row["chunk_id"],
                "chunk_index": row["chunk_index"],
                "path": row["path"],
                "name": row["name"],
                "snippet": _snippet(row["text"], query),
                "score": lexical_score,
                "vector_score": 0.0,
                "lexical_score": lexical_score,
            }
        )
    ranked.sort(key=lambda item: (-item["score"], item["path"], item["chunk_index"]))
    return ranked
