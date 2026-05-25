from __future__ import annotations

from app.indexer.fts_index import FTSIndex
from app.indexer.vector_index import VectorIndex
from app.llm.registry import get_effective_settings
from app.tools.file_tools import find_duplicates, search_by_name


def rebuild_index() -> dict:
    return FTSIndex().rebuild(get_effective_settings().allowed_directories)


def add_directory(path: str) -> dict:
    settings = get_effective_settings()
    dirs = list(dict.fromkeys([*settings.allowed_directories, path]))
    from app.services.settings_service import update_settings

    update_settings({"allowed_directories": dirs})
    return {"allowed_directories": dirs}


def search_files(query: str) -> dict:
    settings = get_effective_settings()
    indexed = FTSIndex().search(query)
    names = search_by_name({"query": query}, {"allowed_directories": settings.allowed_directories})
    return {"index_results": indexed, "name_results": names.get("results", [])}


def semantic_search(query: str, *, limit: int = 10) -> dict:
    return VectorIndex().search(query, limit=limit)


def duplicates() -> dict:
    settings = get_effective_settings()
    indexed = FTSIndex().duplicates()
    live = find_duplicates({}, {"allowed_directories": settings.allowed_directories})
    return {"index_duplicates": indexed, "live_duplicates": live.get("duplicates", [])}
