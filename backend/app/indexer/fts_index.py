from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core import db
from app.core.audit import record
from app.core.paths import resolve_authorized
from app.core.schemas import DocumentChunk, IndexedFile, now_iso
from app.indexer.chunker import chunk_text
from app.indexer.embedding_service import Embedder, embed_texts_sync
from app.indexer.fts_query import fts_match_query
from app.indexer.parsers import parse_file
from app.llm.registry import get_effective_settings
from app.tools.file_tools import sha256_file


logger = logging.getLogger(__name__)


@dataclass
class _PendingChunk:
    chunk: DocumentChunk
    path: str
    text: str


class FTSIndex:
    def __init__(self, *, embedder: Embedder | None = None, embedding_batch_size: int = 64) -> None:
        self.embedder = embedder
        self.embedding_batch_size = max(1, embedding_batch_size)

    def status(self, allowed_directories: list[str] | None = None) -> dict[str, Any]:
        db.init_db()
        allowed_roots = _normalized_allowed_roots(allowed_directories or [])
        if not allowed_roots:
            return {
                "status": "missing_scope",
                "files_indexed": 0,
                "chunks_indexed": 0,
                "embeddings_indexed": 0,
                "bytes_indexed": 0,
                "last_indexed_at": "",
                "last_modified_at": "",
                "latest_failure": None,
                "retry_hint": _index_status_retry_hint("missing_scope"),
            }

        with db.connect() as conn:
            index_rows = conn.execute(
                """
                SELECT id, normalized_path, size, indexed_at, modified_at
                FROM indexed_files
                """
            ).fetchall()
            failure_rows = conn.execute(
                """
                SELECT data, created_at
                FROM audit_events
                WHERE event_type = ?
                ORDER BY created_at DESC, sequence DESC, id DESC
                """,
                ("index.embedding_failed",),
            ).fetchall()

        scoped_rows = [row for row in index_rows if _path_within_roots(str(row["normalized_path"] or ""), allowed_roots)]
        scoped_file_ids = [str(row["id"]) for row in scoped_rows]
        chunks_indexed = 0
        embeddings_indexed = 0
        if scoped_file_ids:
            scoped_file_id_set = set(scoped_file_ids)
            with db.connect() as conn:
                chunk_rows = conn.execute("SELECT file_id, COUNT(*) AS count FROM document_chunks GROUP BY file_id").fetchall()
                embedding_rows = conn.execute(
                    "SELECT file_id, COUNT(*) AS count FROM document_chunk_embeddings GROUP BY file_id"
                ).fetchall()
            chunks_indexed = sum(int(row["count"] or 0) for row in chunk_rows if str(row["file_id"]) in scoped_file_id_set)
            embeddings_indexed = sum(int(row["count"] or 0) for row in embedding_rows if str(row["file_id"]) in scoped_file_id_set)

        files_indexed = len(scoped_rows)
        bytes_indexed = sum(int(row["size"] or 0) for row in scoped_rows)
        last_indexed_at = max((str(row["indexed_at"] or "") for row in scoped_rows), default="")
        last_modified_at = max((str(row["modified_at"] or "") for row in scoped_rows), default="")
        latest_failure = _latest_index_failure(failure_rows, allowed_roots)
        status = "empty" if files_indexed <= 0 else "ready"
        if latest_failure is not None and status == "ready" and _timestamp_after(latest_failure["at"], last_indexed_at):
            status = "degraded"

        return {
            "status": status,
            "files_indexed": files_indexed,
            "chunks_indexed": chunks_indexed,
            "embeddings_indexed": embeddings_indexed,
            "bytes_indexed": bytes_indexed,
            "last_indexed_at": last_indexed_at,
            "last_modified_at": last_modified_at,
            "latest_failure": latest_failure,
            "retry_hint": _index_status_retry_hint(status),
        }

    def rebuild(self, allowed_directories: list[str]) -> dict[str, Any]:
        started = time.perf_counter()
        db.init_db()
        with db.connect() as conn:
            conn.execute("DELETE FROM indexed_files")
            conn.execute("DELETE FROM document_chunks")
            conn.execute("DELETE FROM document_chunk_embeddings")
            try:
                conn.execute("DELETE FROM document_chunks_fts")
            except Exception as exc:
                logger.debug("could not clear optional FTS table: %s", exc, exc_info=True)

        files = 0
        chunks = 0
        embeddings = 0
        embedding_model = get_effective_settings().embedding_model
        pending_files: list[IndexedFile] = []
        pending_chunks: list[_PendingChunk] = []

        def flush_pending() -> None:
            nonlocal embeddings
            if not pending_files and not pending_chunks:
                return

            vectors = embed_texts_sync([item.text for item in pending_chunks], embedder=self.embedder)
            with db.connect() as conn:
                for indexed in pending_files:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO indexed_files
                        (id, normalized_path, data, sha256, name, extension, size, modified_at, indexed_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            indexed.id,
                            indexed.normalized_path,
                            indexed.model_dump_json(),
                            indexed.sha256,
                            indexed.name,
                            indexed.extension,
                            indexed.size,
                            indexed.modified_at,
                            indexed.indexed_at,
                        ),
                    )

                for index, item in enumerate(pending_chunks):
                    vector = vectors[index] if index < len(vectors) else []
                    doc_chunk = item.chunk
                    conn.execute(
                        "INSERT OR REPLACE INTO document_chunks (id, file_id, chunk_index, text, data) VALUES (?, ?, ?, ?, ?)",
                        (
                            doc_chunk.id,
                            doc_chunk.file_id,
                            doc_chunk.chunk_index,
                            doc_chunk.text,
                            doc_chunk.model_dump_json(),
                        ),
                    )
                    try:
                        conn.execute(
                            "INSERT INTO document_chunks_fts (file_id, path, text) VALUES (?, ?, ?)",
                            (doc_chunk.file_id, item.path, item.text),
                        )
                    except Exception as exc:
                        logger.debug("could not insert optional FTS row for %s: %s", item.path, exc, exc_info=True)
                    if vector:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO document_chunk_embeddings
                            (id, chunk_id, file_id, chunk_index, model, dim, embedding, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                doc_chunk.embedding_id,
                                doc_chunk.id,
                                doc_chunk.file_id,
                                doc_chunk.chunk_index,
                                embedding_model,
                                len(vector),
                                json.dumps(vector),
                                now_iso(),
                            ),
                        )
                        embeddings += 1

            pending_files.clear()
            pending_chunks.clear()

        for raw in allowed_directories:
            root = resolve_authorized(raw, allowed_directories)
            candidates = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
            for path in candidates:
                try:
                    normalized = resolve_authorized(path, allowed_directories)
                    stat = normalized.stat()
                    indexed = IndexedFile(
                        path=str(normalized),
                        normalized_path=str(normalized),
                        name=normalized.name,
                        extension=normalized.suffix.lower(),
                        size=stat.st_size,
                        sha256=sha256_file(normalized),
                        created_at=str(stat.st_ctime),
                        modified_at=str(stat.st_mtime),
                    )
                    text = parse_file(normalized)
                    pending_files.append(indexed)
                    for idx, chunk in enumerate(chunk_text(text)):
                        doc_chunk = DocumentChunk(
                            file_id=indexed.id,
                            chunk_index=idx,
                            text=chunk,
                            token_count=max(1, len(chunk) // 4),
                        )
                        doc_chunk.embedding_id = f"emb_{doc_chunk.id}"
                        pending_chunks.append(_PendingChunk(doc_chunk, str(normalized), chunk))
                        chunks += 1
                    if len(pending_chunks) >= self.embedding_batch_size:
                        flush_pending()
                    files += 1
                except Exception:
                    continue
        flush_pending()
        return {
            "files_indexed": files,
            "chunks_indexed": chunks,
            "embeddings_indexed": embeddings,
            "embedding_model": embedding_model,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        }

    def index_file(self, file_path: str | Path, allowed_directories: list[str]) -> bool:
        """Index a single file incrementally. Returns True if the file was indexed."""
        db.init_db()
        normalized = resolve_authorized(file_path, allowed_directories)
        file_hash = sha256_file(normalized)

        # Check if file already indexed with the same hash — skip if unchanged
        with db.connect() as conn:
            existing = conn.execute(
                "SELECT id, sha256 FROM indexed_files WHERE normalized_path = ?",
                (str(normalized),),
            ).fetchone()
            if existing and existing["sha256"] == file_hash:
                return self._backfill_missing_embeddings(
                    str(existing["id"]),
                    normalized,
                ) > 0

        # Remove old entries for this path if they exist
        self.remove_file(str(normalized))

        stat = normalized.stat()
        indexed = IndexedFile(
            path=str(normalized),
            normalized_path=str(normalized),
            name=normalized.name,
            extension=normalized.suffix.lower(),
            size=stat.st_size,
            sha256=file_hash,
            created_at=str(stat.st_ctime),
            modified_at=str(stat.st_mtime),
        )
        text = parse_file(normalized)
        chunks_data: list[_PendingChunk] = []
        for idx, chunk in enumerate(chunk_text(text)):
            doc_chunk = DocumentChunk(
                file_id=indexed.id,
                chunk_index=idx,
                text=chunk,
                token_count=max(1, len(chunk) // 4),
            )
            doc_chunk.embedding_id = f"emb_{doc_chunk.id}"
            chunks_data.append(_PendingChunk(doc_chunk, str(normalized), chunk))

        with db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO indexed_files
                (id, normalized_path, data, sha256, name, extension, size, modified_at, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    indexed.id,
                    indexed.normalized_path,
                    indexed.model_dump_json(),
                    indexed.sha256,
                    indexed.name,
                    indexed.extension,
                    indexed.size,
                    indexed.modified_at,
                    indexed.indexed_at,
                ),
            )
            for item in chunks_data:
                doc_chunk = item.chunk
                conn.execute(
                    "INSERT OR REPLACE INTO document_chunks (id, file_id, chunk_index, text, data) VALUES (?, ?, ?, ?, ?)",
                    (
                        doc_chunk.id,
                        doc_chunk.file_id,
                        doc_chunk.chunk_index,
                        doc_chunk.text,
                        doc_chunk.model_dump_json(),
                    ),
                )
                try:
                    conn.execute(
                        "INSERT INTO document_chunks_fts (file_id, path, text) VALUES (?, ?, ?)",
                        (doc_chunk.file_id, item.path, item.text),
                    )
                except Exception as exc:
                    logger.debug("could not insert optional FTS row for %s: %s", item.path, exc, exc_info=True)

        self._store_embeddings(chunks_data, normalized)
        return True

    def _backfill_missing_embeddings(self, file_id: str, normalized: Path) -> int:
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT dc.id, dc.file_id, dc.chunk_index, dc.text, dc.data
                FROM document_chunks dc
                LEFT JOIN document_chunk_embeddings e ON e.chunk_id = dc.id
                WHERE dc.file_id = ? AND e.chunk_id IS NULL
                ORDER BY dc.chunk_index ASC
                """,
                (file_id,),
            ).fetchall()

        chunks_data: list[_PendingChunk] = []
        for row in rows:
            try:
                doc_chunk = DocumentChunk.model_validate(json.loads(row["data"]))
            except (TypeError, ValueError):
                doc_chunk = DocumentChunk(
                    id=row["id"],
                    file_id=row["file_id"],
                    chunk_index=int(row["chunk_index"]),
                    text=row["text"],
                    token_count=max(1, len(str(row["text"])) // 4),
                )
            if not doc_chunk.embedding_id:
                doc_chunk.embedding_id = f"emb_{doc_chunk.id}"
            chunks_data.append(_PendingChunk(doc_chunk, str(normalized), doc_chunk.text))

        return self._store_embeddings(chunks_data, normalized)

    def _store_embeddings(self, chunks_data: list[_PendingChunk], normalized: Path) -> int:
        if not chunks_data:
            return 0
        try:
            embedding_model = get_effective_settings().embedding_model
            vectors = embed_texts_sync([item.text for item in chunks_data], embedder=self.embedder)
        except Exception as exc:  # noqa: BLE001 - lexical indexing should survive embedding outages.
            logger.warning("embedding generation failed for %s: %s", normalized, exc)
            record(
                "index.embedding_failed",
                "FTSIndex",
                {"path": str(normalized), "error": str(exc)},
            )
            return 0

        inserted = 0
        if vectors:
            with db.connect() as conn:
                for index, item in enumerate(chunks_data):
                    vector = vectors[index] if index < len(vectors) else []
                    if not vector:
                        continue
                    doc_chunk = item.chunk
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO document_chunk_embeddings
                        (id, chunk_id, file_id, chunk_index, model, dim, embedding, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            doc_chunk.embedding_id,
                            doc_chunk.id,
                            doc_chunk.file_id,
                            doc_chunk.chunk_index,
                            embedding_model,
                            len(vector),
                            json.dumps(vector),
                            now_iso(),
                        ),
                    )
                    inserted += 1
        return inserted

    def remove_file(self, normalized_path: str) -> bool:
        """Remove a file and all its chunks from the index. Returns True if something was removed."""
        db.init_db()
        with db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM indexed_files WHERE normalized_path = ?",
                (normalized_path,),
            ).fetchone()
            if not row:
                return False
            file_id = row["id"]
            conn.execute(
                "DELETE FROM document_chunk_embeddings WHERE file_id = ?", (file_id,)
            )
            conn.execute(
                "DELETE FROM document_chunks WHERE file_id = ?", (file_id,)
            )
            try:
                conn.execute(
                    "DELETE FROM document_chunks_fts WHERE file_id = ?", (file_id,)
                )
            except Exception as exc:
                logger.debug("could not delete optional FTS rows for %s: %s", file_id, exc, exc_info=True)
            conn.execute(
                "DELETE FROM indexed_files WHERE id = ?", (file_id,)
            )
        return True

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        db.init_db()
        cleaned = str(query or "").strip()
        with db.connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT file_id, path, snippet(document_chunks_fts, 2, '[', ']', '...', 12) AS snippet FROM document_chunks_fts WHERE document_chunks_fts MATCH ? LIMIT ?",
                    (fts_match_query(cleaned), limit),
                ).fetchall()
                if rows or len(cleaned) >= 3:
                    return [dict(row) for row in rows]
            except Exception as exc:
                logger.info("FTS search failed; falling back to LIKE for query=%r: %s", cleaned, exc)
            return self._search_like(conn, cleaned, limit)

    def _search_like(self, conn, query: str, limit: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT dc.file_id, dc.text, f.data FROM document_chunks dc JOIN indexed_files f ON f.id = dc.file_id WHERE dc.text LIKE ? LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        results = []
        for row in rows:
            file_data = json.loads(row["data"])
            results.append({"file_id": row["file_id"], "path": file_data["path"], "snippet": row["text"][:240]})
        return results

    def duplicates(self) -> list[dict[str, Any]]:
        db.init_db()
        with db.connect() as conn:
            rows = conn.execute("SELECT data, sha256 FROM indexed_files").fetchall()
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(row["sha256"], []).append(json.loads(row["data"]))
        return [{"sha256": digest, "files": files} for digest, files in groups.items() if len(files) > 1]


class SearchIndex:
    def __init__(self) -> None:
        self.docs: list[tuple[str, str]] = []

    def add_document(self, path: str, text: str) -> None:
        self.docs.append((path, text))

    def search(self, query: str) -> list[dict[str, str]]:
        return [{"path": path, "text": text} for path, text in self.docs if query.lower() in text.lower()]


def _normalized_allowed_roots(allowed_directories: list[str]) -> list[Path]:
    roots: list[Path] = []
    for raw_root in allowed_directories:
        if not str(raw_root).strip():
            continue
        try:
            roots.append(Path(raw_root).expanduser().resolve(strict=False))
        except OSError:
            continue
    return roots


def _path_within_roots(path: str, roots: list[Path]) -> bool:
    if not path:
        return False
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except OSError:
        return False
    for root in roots:
        if resolved == root or root in resolved.parents:
            return True
    return False


def _latest_index_failure(rows: list[Any], allowed_roots: list[Path]) -> dict[str, str] | None:
    for row in rows:
        try:
            data = json.loads(row["data"])
        except (TypeError, ValueError, KeyError):
            data = {}
        payload = data.get("payload") if isinstance(data, dict) else {}
        payload = payload if isinstance(payload, dict) else {}
        path = str(payload.get("path") or "")
        if not _path_within_roots(path, allowed_roots):
            continue
        message = _safe_index_failure_message(payload.get("error"))
        return {
            "at": str(row["created_at"] or data.get("created_at") or ""),
            "path_label": Path(path).name,
            "message": message,
        }
    return None


def _safe_index_failure_message(value: Any) -> str:
    message = str(value or "").strip()
    if not message:
        return "Indexing could not finish semantic embeddings."
    lowered = message.lower()
    if any(marker in lowered for marker in ("token", "secret", "password", "api key", "apikey")):
        return "Indexing failed recently; details were redacted."
    if "\\" in message or "/" in message or "://" in message:
        return "Indexing failed recently; path details were redacted."
    return message[:180]


def _index_status_retry_hint(status: str) -> str:
    if status == "missing_scope":
        return "Choose an authorized folder before indexing or searching files."
    if status == "empty":
        return "The content index is empty. File-name search still scans live files; rebuild the index to search inside documents."
    if status == "degraded":
        return "The content index is usable, but semantic indexing failed recently. Retry rebuild after the local embedding service recovers."
    return ""


def _timestamp_after(left: str, right: str) -> bool:
    if not right:
        return True
    try:
        left_at = datetime.fromisoformat(left)
        right_at = datetime.fromisoformat(right)
    except ValueError:
        return True
    return left_at >= right_at
