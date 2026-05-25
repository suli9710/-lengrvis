from __future__ import annotations

import json
import math
import re
from typing import Any

from app.core import db
from app.indexer.embedding_service import Embedder, embed_texts_sync


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
    ) -> dict[str, Any]:
        db.init_db()
        query = str(query or "").strip()
        if not query:
            return {"query": query, "results": [], "count": 0, "candidate_count": 0, "source": "vector"}

        query_vector = embed_texts_sync([query], embedder=self.embedder)[0]
        candidates = self._candidate_chunks(query, candidate_limit)
        source = "fts_vector_rerank"
        if not candidates:
            candidates = self._recent_chunks(scan_limit)
            source = "vector_scan"

        ranked = []
        for row in candidates:
            vector = _loads_vector(row.get("embedding"))
            if not vector:
                continue
            vector_score = _cosine_similarity(query_vector, vector)
            lexical_score = float(row.get("lexical_score") or 0.0)
            score = vector_score + min(0.2, lexical_score / 100.0)
            ranked.append(
                {
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
            )

        ranked.sort(key=lambda item: (-item["score"], item["path"], item["chunk_index"]))
        collapsed = _collapse_by_file(ranked, limit)
        return {
            "query": query,
            "results": collapsed,
            "count": len(collapsed),
            "candidate_count": len(candidates),
            "source": source,
        }

    def _candidate_chunks(self, query: str, limit: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
            try:
                fts_rows = conn.execute(
                    """
                    SELECT file_id, bm25(document_chunks_fts) AS rank
                    FROM document_chunks_fts
                    WHERE document_chunks_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (_fts_query(query), limit),
                ).fetchall()
            except Exception:
                fts_rows = []

            if fts_rows:
                file_scores: dict[str, float] = {}
                for row in fts_rows:
                    score = 1.0 / (1.0 + abs(float(row["rank"] or 0.0)))
                    file_scores[row["file_id"]] = max(file_scores.get(row["file_id"], 0.0), score)
                return self._chunks_for_files(conn, file_scores, limit)

            like_rows = conn.execute(
                """
                SELECT dc.file_id
                FROM document_chunks dc
                WHERE dc.text LIKE ?
                LIMIT ?
                """,
                (f"%{query}%", limit),
            ).fetchall()
            file_scores = {row["file_id"]: 1.0 for row in like_rows}
            return self._chunks_for_files(conn, file_scores, limit)

    def _recent_chunks(self, limit: int) -> list[dict[str, Any]]:
        with db.connect() as conn:
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
                ORDER BY f.indexed_at DESC, dc.chunk_index ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _chunks_for_files(self, conn, file_scores: dict[str, float], limit: int) -> list[dict[str, Any]]:
        if not file_scores:
            return []
        placeholders = ",".join("?" for _ in file_scores)
        rows = conn.execute(
            f"""
            SELECT
                dc.file_id,
                dc.id AS chunk_id,
                dc.chunk_index,
                dc.text,
                f.name,
                f.normalized_path AS path,
                e.embedding
            FROM document_chunks dc
            JOIN indexed_files f ON f.id = dc.file_id
            JOIN document_chunk_embeddings e ON e.chunk_id = dc.id
            WHERE dc.file_id IN ({placeholders})
            ORDER BY dc.chunk_index ASC
            LIMIT ?
            """,
            (*file_scores.keys(), limit),
        ).fetchall()
        candidates = []
        for row in rows:
            item = dict(row)
            item["lexical_score"] = file_scores.get(item["file_id"], 0.0)
            candidates.append(item)
        return candidates


def _loads_vector(raw: Any) -> list[float]:
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return []
    else:
        data = raw
    if not isinstance(data, list):
        return []
    return [float(value) for value in data]


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\w]+", query, flags=re.UNICODE)
    if not tokens:
        return query
    return " OR ".join(f'"{token}"' for token in tokens[:8])


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    pairs = list(zip(left, right))
    if not pairs:
        return 0.0
    dot = sum(a * b for a, b in pairs)
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


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
