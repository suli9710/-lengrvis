from __future__ import annotations

from fastapi import APIRouter, Query

from app.services import file_service


router = APIRouter()


@router.post("/index/rebuild")
def rebuild_index():
    return file_service.rebuild_index()


@router.post("/index/add-directory")
def add_directory(payload: dict):
    return file_service.add_directory(str(payload.get("path", "")))


@router.get("/files/search")
def search(q: str = Query("")):
    return file_service.search_files(q)


@router.get("/files/semantic-search")
def semantic_search(q: str = Query("")):
    return file_service.semantic_search(q)


@router.get("/files/duplicates")
def duplicates():
    return file_service.duplicates()


@router.get("/files/{file_id}")
def file_detail(file_id: str):
    return {"id": file_id, "note": "File detail endpoint is reserved for indexed metadata lookup."}


@router.post("/files/preview-operation")
def preview_operation(payload: dict):
    return {"dry_run": True, "diff_preview": payload}


@router.post("/files/cluster")
def cluster_files(payload: dict | None = None):
    from app.llm.registry import get_effective_settings
    from app.tools.registry import register_all_tools, registry as tool_registry

    if not tool_registry.list():
        register_all_tools()

    settings = get_effective_settings()
    context = {"allowed_directories": settings.allowed_directories, "settings": settings}
    args: dict = {}
    payload = payload or {}
    if payload.get("k"):
        try:
            args["k"] = int(payload["k"])
        except (TypeError, ValueError):
            pass
    for key in ("group_by", "cluster_by", "paths", "image_paths", "images", "limit", "metadata_weight"):
        if key in payload:
            args[key] = payload[key]
    group_by = str(payload.get("group_by") or "").strip().lower()
    image_grouping = group_by in {"image", "images", "scene", "people", "objects", "tags", "time", "location"}
    if group_by in {"image", "images"}:
        args["group_by"] = payload.get("cluster_by") or "auto"
    tool = tool_registry.get("image.cluster_images" if image_grouping else "file.cluster_by_content")
    return tool.execute(args, context)
