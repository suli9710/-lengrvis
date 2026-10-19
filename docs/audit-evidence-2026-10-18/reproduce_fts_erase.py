"""Reproduce indexed document text surviving the privacy erase in the FTS table."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("LENGRVIS_AUDIT_HMAC_SECRET", "review-repro-secret")
data_dir = Path(tempfile.mkdtemp(prefix="lengrvis-fts-erase-"))
os.environ["LENGRVIS_DATA_DIR"] = str(data_dir)

from app.core import db  # noqa: E402

MARKER = "ERASED_DOCUMENT_MARKER_7731"
db.init_db()
with db.connect() as conn:
    conn.execute(
        "INSERT INTO indexed_files(id,normalized_path,data,sha256,name,extension,size,modified_at,indexed_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        ("f1", "C:/docs/secret.txt", "{}", "abc", "secret.txt", ".txt", 10, "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO document_chunks(id,file_id,chunk_index,text,data) VALUES(?,?,?,?,?)",
        ("c1", "f1", 0, f"{MARKER} body text", "{}"),
    )
    conn.execute(
        "INSERT INTO document_chunks_fts(file_id,path,text,chunk_id) VALUES(?,?,?,?)",
        ("f1", "C:/docs/secret.txt", f"{MARKER} body text", "c1"),
    )

db.erase_local_user_data()
with db.connect() as conn:
    fts_count = conn.execute("SELECT COUNT(*) FROM document_chunks_fts").fetchone()[0]
    fts_text = conn.execute("SELECT text FROM document_chunks_fts").fetchone()
print(f"fts_rows_after_erase={fts_count}")
print(f"fts_text_after_erase={fts_text[0] if fts_text else None!r}")
raw = (data_dir / "lengrvis.db").read_bytes()
print(f"marker_bytes_in_db_file={MARKER.encode() in raw}")
if fts_count != 0:
    print("FTS_RESIDUE_CONFIRMED")
