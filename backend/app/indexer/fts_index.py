from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core import db
from app.core.paths import resolve_authorized
from app.core.schemas import DocumentChunk, IndexedFile, now_iso
from app.indexer.chunker import chunk_text
from app.indexer.embedding_service import Embedder, embed_texts_sync
from app.indexer.parsers import parse_file
from app.llm.registry import get_effective_settings
from app.tools.file_tools import sha256_file


@dataclass
class _PendingChunk:
    chunk: DocumentChunk
    path: str
    text: str


class FTSIndex:
    def __init__(self, *, embedder: Embedder | None = None, embedding_batch_size: int = 64) -> None:
        self.embedder = embedder
        self.embedding_batch_size = max(1, embedding_batch_size)

    def rebuild(self, allowed_directories: list[str]) -> dict[str, Any]:
        started = time.perf_counter()
        db.init_db()
        with db.connect() as conn:
            conn.execute("DELETE FROM indexed_files")
            conn.execute("DELETE FROM document_chunks")
            conn.execute("DELETE FROM document_chunk_embeddings")
            try:
                conn.execute("DELETE FROM document_chunks_fts")
            except Exception:
                pass

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
                    except Exception:
                        pass
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
                "SELECT sha256 FROM indexed_files WHERE normalized_path = ?",
                (str(normalized),),
            ).fetchone()
            if existing and existing["sha256"] == file_hash:
                return False

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

        embedding_model = get_effective_settings().embedding_model
        vectors = embed_texts_sync([item.text for item in chunks_data], embedder=self.embedder)

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
            for index, item in enumerate(chunks_data):
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
                except Exception:
                    pass
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
        return True

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
            except Exception:
                pass
            conn.execute(
                "DELETE FROM indexed_files WHERE id = ?", (file_id,)
            )
        return True

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        db.init_db()
        with db.connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT file_id, path, snippet(document_chunks_fts, 2, '[', ']', '...', 12) AS snippet FROM document_chunks_fts WHERE document_chunks_fts MATCH ? LIMIT ?",
                    (query, limit),
                ).fetchall()
                return [dict(row) for row in rows]
            except Exception:
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
