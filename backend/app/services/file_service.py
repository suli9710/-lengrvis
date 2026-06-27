from __future__ import annotations

from pathlib import Path

from app.indexer.fts_index import FTSIndex
from app.indexer.vector_index import VectorIndex
from app.llm.registry import get_effective_settings
from app.tools.file_tools import find_duplicates, search_by_name


def rebuild_index() -> dict:
    return FTSIndex().rebuild(get_effective_settings().allowed_directories)


def add_directory(path: str, *, confirmation_nonce: str | None = None) -> dict:
    settings = get_effective_settings()
    dirs = list(dict.fromkeys([*settings.allowed_directories, path]))
    from app.services.settings_service import update_settings

    update_settings({"allowed_directories": dirs, "confirmation_nonce": confirmation_nonce})
    return {"allowed_directories": dirs}


def search_files(query: str) -> dict:
    settings = get_effective_settings()
    normalized_query = query.strip()
    index = FTSIndex()
    index_status = index.status(settings.allowed_directories)
    if not settings.allowed_directories:
        return {
            "index_results": [],
            "name_results": [],
            "index_status": index_status,
            "name_search": {
                "count": 0,
                "scanned": 0,
                "truncated": False,
                "status": "missing_scope",
            },
        }
    if not normalized_query:
        return {
            "index_results": [],
            "name_results": [],
            "index_status": index_status,
            "name_search": {
                "count": 0,
                "scanned": 0,
                "truncated": False,
                "status": "empty_query",
            },
        }

    indexed = index.search(normalized_query, allowed_directories=settings.allowed_directories)
    names = search_by_name(
        {"query": normalized_query, "limit": 100, "max_scanned": 5000},
        {"allowed_directories": settings.allowed_directories},
    )
    return {
        "index_results": indexed,
        "name_results": names.get("results", []),
        "index_status": index_status,
        "name_search": {
            "count": names.get("count", 0),
            "scanned": names.get("scanned", 0),
            "truncated": bool(names.get("truncated", False)),
        },
    }


def _within_allowed_directories(path: str, allowed_directories: list[str]) -> bool:
    if not path:
        return False
    try:
        resolved = Path(path).expanduser().resolve(strict=False)
    except OSError:
        return False
    for raw_base in allowed_directories:
        try:
            base = Path(raw_base).expanduser().resolve(strict=False)
        except OSError:
            continue
        if resolved == base or base in resolved.parents:
            return True
    return False


def semantic_search(query: str, *, limit: int = 10) -> dict:
    settings = get_effective_settings()
    return VectorIndex().search(query, limit=limit, allowed_directories=settings.allowed_directories)


def duplicates() -> dict:
    settings = get_effective_settings()
    indexed = FTSIndex().duplicates(settings.allowed_directories)
    if not settings.allowed_directories:
        live = {
            "duplicates": [],
            "count": 0,
            "scanned": 0,
            "truncated": False,
            "skipped_large": 0,
            "status": "missing_scope",
        }
    else:
        live = find_duplicates(
            {"limit": 100, "max_scanned": 5000, "max_file_bytes": 100 * 1024 * 1024},
            {"allowed_directories": settings.allowed_directories},
        )
    return {
        "index_duplicates": indexed,
        "live_duplicates": live.get("duplicates", []),
        "live_duplicates_meta": {
            "count": live.get("count", 0),
            "scanned": live.get("scanned", 0),
            "truncated": bool(live.get("truncated", False)),
            "skipped_large": live.get("skipped_large", 0),
            "status": live.get("status", "ok"),
        },
    }
