from __future__ import annotations

import json
import math
import re
from pathlib import Path
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
        allowed_directories: list[str] | None = None,
    ) -> dict[str, Any]:
        db.init_db()
        query = str(query or "").strip()
        if not query:
            return {"query": query, "results": [], "count": 0, "candidate_count": 0, "source": "vector"}
        allowed_bases = _allowed_bases(allowed_directories)
        if allowed_bases == []:
            return {"query": query, "results": [], "count": 0, "candidate_count": 0, "source": "vector"}

        query_vector = embed_texts_sync([query], embedder=self.embedder)[0]
        candidates = _filter_allowed_rows(self._candidate_chunks(query, candidate_limit), allowed_bases)
        source = "fts_vector_rerank"
        if not candidates:
            lexical_candidates = _filter_allowed_rows(self._lexical_chunks(query, candidate_limit), allowed_bases)
            if lexical_candidates:
                fallback_results = _collapse_by_file(
                    _lexical_fallback_rows(lexical_candidates, query),
                    limit,
                )
                return {
                    "query": query,
                    "results": fallback_results,
                    "count": len(fallback_results),
                    "candidate_count": len(lexical_candidates),
                    "source": "fts_lexical_fallback",
                }
            candidates = _filter_allowed_rows(self._recent_chunks(scan_limit), allowed_bases)
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

        lexical_candidates = _filter_allowed_rows(self._lexical_chunks(query, candidate_limit), allowed_bases)
        embedded_chunk_ids = {row["chunk_id"] for row in candidates}
        missing_embedding_candidates = [
            row
            for row in lexical_candidates
            if row["chunk_id"] not in embedded_chunk_ids and not _loads_vector(row.get("embedding"))
        ]
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

    def _lexical_chunks(self, query: str, limit: int) -> list[dict[str, Any]]:
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
                return self._chunks_for_files(
                    conn,
                    file_scores,
                    limit,
                    require_embeddings=False,
                )

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
            return self._chunks_for_files(
                conn,
                file_scores,
                limit,
                require_embeddings=False,
            )

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

    def _chunks_for_files(
        self,
        conn,
        file_scores: dict[str, float],
        limit: int,
        *,
        require_embeddings: bool = True,
    ) -> list[dict[str, Any]]:
        if not file_scores:
            return []
        placeholders = ",".join("?" for _ in file_scores)
        embedding_join = "JOIN document_chunk_embeddings e ON e.chunk_id = dc.id"
        if not require_embeddings:
            embedding_join = "LEFT JOIN document_chunk_embeddings e ON e.chunk_id = dc.id"
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
            {embedding_join}
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


def _allowed_bases(allowed_directories: list[str] | None) -> list[Path] | None:
    if allowed_directories is None:
        return None
    bases: list[Path] = []
    for raw_base in allowed_directories:
        try:
            bases.append(Path(raw_base).expanduser().resolve(strict=False))
        except OSError:
            continue
    return bases


def _filter_allowed_rows(rows: list[dict[str, Any]], allowed_bases: list[Path] | None) -> list[dict[str, Any]]:
    if allowed_bases is None:
        return rows
    return [row for row in rows if _within_allowed_bases(str(row.get("path") or ""), allowed_bases)]


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
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
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
