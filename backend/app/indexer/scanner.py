from __future__ import annotations

from app.indexer.fts_index import FTSIndex


def rebuild_index(allowed_directories: list[str]):
    return FTSIndex().rebuild(allowed_directories)

