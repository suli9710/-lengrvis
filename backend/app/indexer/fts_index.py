from __future__ import annotations

import json
import logging
import sqlite3
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

INDEX_FAILURE_EVENT_TYPES = ("index.embedding_failed", "index.rebuild_file_failed")
MAX_REBUILD_FAILURES_REPORTED = 20


@dataclass
class _PendingChunk:
    chunk: DocumentChunk
    path: str
    text: str


@dataclass
class _PreparedEmbedding:
    id: str
    chunk_id: str
    file_id: str
    chunk_index: int
    model: str
    dim: int
    embedding: str
    created_at: str


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
                WHERE event_type IN (?, ?)
                ORDER BY created_at DESC, sequence DESC, id DESC
                """,
                INDEX_FAILURE_EVENT_TYPES,
            ).fetchall()

        scoped_rows = [
            row for row in index_rows if _path_within_roots(str(row["normalized_path"] or ""), allowed_roots)
        ]
        scoped_file_ids = [str(row["id"]) for row in scoped_rows]
        chunks_indexed = 0
        embeddings_indexed = 0
        if scoped_file_ids:
            scoped_file_id_set = set(scoped_file_ids)
            with db.connect() as conn:
                chunk_rows = conn.execute(
                    "SELECT file_id, COUNT(*) AS count FROM document_chunks GROUP BY file_id"
                ).fetchall()
                embedding_rows = conn.execute(
                    "SELECT file_id, COUNT(*) AS count FROM document_chunk_embeddings GROUP BY file_id"
                ).fetchall()
            chunks_indexed = sum(
                int(row["count"] or 0) for row in chunk_rows if str(row["file_id"]) in scoped_file_id_set
            )
            embeddings_indexed = sum(
                int(row["count"] or 0) for row in embedding_rows if str(row["file_id"]) in scoped_file_id_set
            )

        files_indexed = len(scoped_rows)
        bytes_indexed = sum(int(row["size"] or 0) for row in scoped_rows)
        last_indexed_at = max((str(row["indexed_at"] or "") for row in scoped_rows), default="")
        last_modified_at = max((str(row["modified_at"] or "") for row in scoped_rows), default="")
        latest_failure = _latest_index_failure(failure_rows, allowed_roots)
        status = "empty" if files_indexed <= 0 else "ready"
        if (
            latest_failure is not None
            and status in {"empty", "ready"}
            and _timestamp_after(latest_failure["at"], last_indexed_at)
        ):
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
        settings = get_effective_settings()
        files = 0
        chunks = 0
        embeddings = 0
        bytes_indexed = 0
        embedding_model = settings.embedding_model
        max_files = max(1, int(getattr(settings, "index_rebuild_max_files", 25000)))
        max_bytes = max(1, int(getattr(settings, "index_rebuild_max_bytes", 2 * 1024 * 1024 * 1024)))
        prepared_files: list[IndexedFile] = []
        prepared_chunks: list[_PendingChunk] = []
        prepared_embeddings: list[_PreparedEmbedding] = []
        pending_files: list[IndexedFile] = []
        pending_chunks: list[_PendingChunk] = []
        failures_reported: list[dict[str, str]] = []
        files_failed = 0
        aborted = False
        abort_reason = ""

        def record_rebuild_failure(path: str | Path, exc: Exception) -> None:
            nonlocal files_failed
            files_failed += 1
            path_text = str(path)
            logger.warning("Skipping file during index rebuild: %s: %s", path_text, exc, exc_info=True)
            record(
                "index.rebuild_file_failed",
                "FTSIndex",
                {"path": path_text, "error": str(exc)},
            )
            if len(failures_reported) < MAX_REBUILD_FAILURES_REPORTED:
                failures_reported.append(
                    {
                        "path_label": Path(path_text).name or path_text[:80],
                        "message": _safe_index_failure_message(exc),
                    }
                )

        def result_payload() -> dict[str, Any]:
            return {
                "files_indexed": files,
                "chunks_indexed": chunks,
                "embeddings_indexed": embeddings,
                "bytes_indexed": bytes_indexed,
                "files_failed": files_failed,
                "failures": failures_reported,
                "embedding_model": embedding_model,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "aborted": aborted,
                "abort_reason": abort_reason,
                "limits": {
                    "max_files": max_files,
                    "max_bytes": max_bytes,
                },
            }

        def abort_rebuild(reason: str, path: str | Path) -> None:
            nonlocal aborted, abort_reason
            aborted = True
            abort_reason = reason
            path_text = str(path)
            record("index.rebuild_aborted", "FTSIndex", {"path": path_text, "reason": reason})
            if len(failures_reported) < MAX_REBUILD_FAILURES_REPORTED:
                failures_reported.append({"path_label": Path(path_text).name or path_text[:80], "message": reason})

        valid_roots: list[Path] = []
        for raw in allowed_directories:
            try:
                valid_roots.append(resolve_authorized(raw, allowed_directories))
            except Exception as exc:  # noqa: BLE001 - rebuild reports authorization failures.
                record_rebuild_failure(raw, exc)

        if not valid_roots:
            return result_payload()

        def flush_pending() -> None:
            nonlocal embeddings
            if not pending_files and not pending_chunks:
                return

            try:
                vectors = embed_texts_sync([item.text for item in pending_chunks], embedder=self.embedder)
            except Exception as exc:  # noqa: BLE001 - lexical rebuild should survive embedding outages.
                logger.warning("embedding generation failed during index rebuild: %s", exc)
                record(
                    "index.embedding_failed",
                    "FTSIndex",
                    {"path": "rebuild", "error": str(exc), "chunks": len(pending_chunks)},
                )
                vectors = []

            prepared_files.extend(pending_files)
            for index, item in enumerate(pending_chunks):
                prepared_chunks.append(item)
                vector = vectors[index] if index < len(vectors) else []
                if vector:
                    doc_chunk = item.chunk
                    prepared_embeddings.append(
                        _PreparedEmbedding(
                            id=str(doc_chunk.embedding_id or f"emb_{doc_chunk.id}"),
                            chunk_id=doc_chunk.id,
                            file_id=doc_chunk.file_id,
                            chunk_index=doc_chunk.chunk_index,
                            model=embedding_model,
                            dim=len(vector),
                            embedding=json.dumps(vector),
                            created_at=now_iso(),
                        )
                    )
                    embeddings += 1

            pending_files.clear()
            pending_chunks.clear()

        for root in valid_roots:
            try:
                candidates = (root,) if root.is_file() else root.rglob("*")
                for path in candidates:
                    if aborted:
                        break
                    if not path.is_file():
                        continue
                    pending_file_start = len(pending_files)
                    pending_chunk_start = len(pending_chunks)
                    chunks_start = chunks
                    bytes_start = bytes_indexed
                    try:
                        normalized = resolve_authorized(path, allowed_directories)
                        stat = normalized.stat()
                        if files >= max_files:
                            abort_rebuild(f"Index rebuild file limit exceeded ({max_files}).", normalized)
                            break
                        if bytes_indexed + int(stat.st_size) > max_bytes:
                            abort_rebuild(f"Index rebuild byte limit exceeded ({max_bytes}).", normalized)
                            break
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
                        bytes_indexed += int(stat.st_size)
                    except Exception as exc:  # noqa: BLE001 - one bad parser/file must not stop rebuild.
                        del pending_files[pending_file_start:]
                        del pending_chunks[pending_chunk_start:]
                        chunks = chunks_start
                        bytes_indexed = bytes_start
                        record_rebuild_failure(path, exc)
                        continue
            except Exception as exc:  # noqa: BLE001 - rebuild reports authorization/enumeration failures.
                record_rebuild_failure(root, exc)
                continue
            if aborted:
                break
        if aborted:
            return result_payload()
        flush_pending()

        with db.connect() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM indexed_files")
            conn.execute("DELETE FROM document_chunks")
            conn.execute("DELETE FROM document_chunk_embeddings")
            try:
                conn.execute("DELETE FROM document_chunks_fts")
            except sqlite3.Error as exc:
                logger.debug("could not clear optional FTS table: %s", exc, exc_info=True)

            for indexed in prepared_files:
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

            for item in prepared_chunks:
                doc_chunk = item.chunk
                conn.execute(
                    """
                    INSERT OR REPLACE INTO document_chunks
                    (id, file_id, chunk_index, text, data)
                    VALUES (?, ?, ?, ?, ?)
                    """,
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
                except sqlite3.Error as exc:
                    logger.debug("could not insert optional FTS row for %s: %s", item.path, exc, exc_info=True)

            for embedding in prepared_embeddings:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO document_chunk_embeddings
                    (id, chunk_id, file_id, chunk_index, model, dim, embedding, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        embedding.id,
                        embedding.chunk_id,
                        embedding.file_id,
                        embedding.chunk_index,
                        embedding.model,
                        embedding.dim,
                        embedding.embedding,
                        embedding.created_at,
                    ),
                )

        return result_payload()

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
                return (
                    self._backfill_missing_embeddings(
                        str(existing["id"]),
                        normalized,
                    )
                    > 0
                )

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
                    """
                    INSERT OR REPLACE INTO document_chunks
                    (id, file_id, chunk_index, text, data)
                    VALUES (?, ?, ?, ?, ?)
                    """,
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
                except sqlite3.Error as exc:
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
            conn.execute("DELETE FROM document_chunk_embeddings WHERE file_id = ?", (file_id,))
            conn.execute("DELETE FROM document_chunks WHERE file_id = ?", (file_id,))
            try:
                conn.execute("DELETE FROM document_chunks_fts WHERE file_id = ?", (file_id,))
            except sqlite3.Error as exc:
                logger.debug("could not delete optional FTS rows for %s: %s", file_id, exc, exc_info=True)
            conn.execute("DELETE FROM indexed_files WHERE id = ?", (file_id,))
        return True

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        db.init_db()
        cleaned = str(query or "").strip()
        with db.connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT file_id, path,
                           snippet(document_chunks_fts, 2, '[', ']', '...', 12) AS snippet
                    FROM document_chunks_fts
                    WHERE document_chunks_fts MATCH ?
                    LIMIT ?
                    """,
                    (fts_match_query(cleaned), limit),
                ).fetchall()
                if rows or len(cleaned) >= 3:
                    return [dict(row) for row in rows]
            except sqlite3.Error as exc:
                logger.info("FTS search failed; falling back to LIKE for query=%r: %s", cleaned, exc)
            return self._search_like(conn, cleaned, limit)

    def _search_like(self, conn, query: str, limit: int) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT dc.file_id, dc.text, f.data
            FROM document_chunks dc
            JOIN indexed_files f ON f.id = dc.file_id
            WHERE dc.text LIKE ?
            LIMIT ?
            """,
            (f"%{query}%", limit),
        ).fetchall()
        results = []
        for row in rows:
            file_data = json.loads(row["data"])
            results.append({"file_id": row["file_id"], "path": file_data["path"], "snippet": row["text"][:240]})
        return results

    def duplicates(self, allowed_directories: list[str] | None = None) -> list[dict[str, Any]]:
        db.init_db()
        allowed_roots = _normalized_allowed_roots(allowed_directories or [])
        if not allowed_roots:
            return []
        with db.connect() as conn:
            rows = conn.execute("SELECT data, normalized_path, sha256 FROM indexed_files").fetchall()
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            file_data = json.loads(row["data"])
            indexed_path = str(
                row["normalized_path"] or file_data.get("normalized_path") or file_data.get("path") or ""
            )
            if not _path_within_roots(indexed_path, allowed_roots):
                continue
            groups.setdefault(row["sha256"], []).append(file_data)
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
        return (
            "The content index is empty. File-name search still scans live files; "
            "rebuild the index to search inside documents."
        )
    if status == "degraded":
        return (
            "The content index is usable, but semantic indexing failed recently. "
            "Retry rebuild after the local embedding service recovers."
        )
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
