from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.agents.safety_review_agent import SafetyReviewAgent
from app.core.errors import SecurityError
from app.core.schemas import SafetyReview
from app.llm.registry import get_effective_settings
from app.policy.risk import RiskLevel, SafetyVerdict
from app.services import file_service
from app.services.local_library_service import list_local_library, preview_local_image
from app.tools import file_tools
from app.tools.registry import register_all_tools
from app.tools.registry import registry as tool_registry

router = APIRouter()


def _tool_context() -> dict:
    settings = get_effective_settings()
    return {"allowed_directories": settings.allowed_directories, "settings": settings}


def _tool_definition(tool_name: str):
    if not tool_registry.list():
        register_all_tools()
    return tool_registry.get(tool_name)


def _blocked_review_response(review: SafetyReview) -> dict | None:
    if review.verdict == SafetyVerdict.DENY:
        return {
            "ok": False,
            "status": "denied",
            "error": "; ".join(review.reasons) or review.safe_alternative or "Tool call denied.",
            "review": review.model_dump(mode="json"),
        }
    if review.verdict == SafetyVerdict.NEEDS_USER_APPROVAL:
        return {
            "ok": False,
            "status": "requires_approval",
            "requires_approval": True,
            "paused": True,
            "review": review.model_dump(mode="json"),
        }
    return None


def _review_cleanup_tool(tool_name: str, payload: dict, context: dict) -> dict | None:
    tool = _tool_definition(tool_name)
    review = SafetyReviewAgent(settings=context.get("settings")).review_tool_call(
        "direct_file_api",
        None,
        tool.name,
        payload,
        tool.risk_level if tool.risk_level else RiskLevel.R3_DESTRUCTIVE_OR_SYSTEM,
        context=context,
        tool_definition=tool,
    )
    return _blocked_review_response(review)


def _blocked_live_cleanup_response(tool_name: str, payload: dict) -> dict:
    if payload.get("approved") or payload.get("approval_id"):
        return {
            "ok": False,
            "status": "denied",
            "tool_name": tool_name,
            "error": (
                f"{tool_name} direct file API cannot consume approval fields. "
                "Run live cleanup through the approved tool runtime."
            ),
        }
    return {
        "ok": False,
        "status": "requires_approval",
        "requires_approval": True,
        "paused": True,
        "tool_name": tool_name,
        "error": (
            f"{tool_name} live execution is not available through the direct file API. "
            "Run it through the approved tool runtime so the approval claim, ledger, and audit trail stay bound."
        ),
    }


@router.post("/index/rebuild")
def rebuild_index():
    return file_service.rebuild_index()


@router.post("/index/add-directory")
def add_directory(payload: dict):
    return file_service.add_directory(
        str(payload.get("path", "")),
        confirmation_nonce=str(payload.get("confirmation_nonce") or ""),
    )


@router.get("/files/search")
def search(q: str = Query("")):
    return file_service.search_files(q)


@router.get("/files/semantic-search")
def semantic_search(q: str = Query("")):
    return file_service.semantic_search(q)


@router.get("/files/duplicates")
def duplicates():
    return file_service.duplicates()


@router.get("/library")
def local_library(
    section: str = Query("gallery"),
    q: str = Query(""),
    limit: int = Query(240, ge=1, le=500),
):
    return list_local_library(section=section, query=q, limit=limit)


@router.get("/library/preview")
def local_library_preview(path: str = Query(...)):
    return preview_local_image(path)


@router.get("/files/{file_id}")
def file_detail(file_id: str):
    return {"id": file_id, "note": "File detail endpoint is reserved for indexed metadata lookup."}


@router.post("/files/preview-operation")
def preview_operation(payload: dict):
    return {"dry_run": True, "diff_preview": payload}


@router.post("/files/cleanup/scan")
def cleanup_scan(payload: dict | None = None):
    return file_tools.cleanup_scan(payload or {}, _tool_context())


@router.post("/files/cleanup/plan")
def cleanup_plan(payload: dict | None = None):
    return file_tools.cleanup_plan(payload or {}, _tool_context())


@router.post("/files/cleanup/execute")
def cleanup_execute(payload: dict | None = None):
    payload = payload or {}
    context = _tool_context()
    if payload.get("dry_run") is False and (payload.get("approved") or payload.get("approval_id")):
        return _blocked_live_cleanup_response("file.cleanup_execute", payload)
    blocked = _review_cleanup_tool("file.cleanup_execute", payload, context)
    if blocked is not None:
        return blocked
    if payload.get("dry_run") is False:
        return _blocked_live_cleanup_response("file.cleanup_execute", payload)
    try:
        return file_tools.cleanup_execute(payload, context)
    except SecurityError as exc:
        return {"ok": False, "status": "denied", "error": exc.message}


@router.post("/files/cleanup/rollback")
def cleanup_rollback(payload: dict | None = None):
    payload = payload or {}
    context = _tool_context()
    if payload.get("dry_run") is False and (payload.get("approved") or payload.get("approval_id")):
        return _blocked_live_cleanup_response("file.cleanup_rollback", payload)
    blocked = _review_cleanup_tool("file.cleanup_rollback", payload, context)
    if blocked is not None:
        return blocked
    if payload.get("dry_run") is False:
        return _blocked_live_cleanup_response("file.cleanup_rollback", payload)
    try:
        return file_tools.cleanup_rollback(payload, context)
    except SecurityError as exc:
        return {"ok": False, "status": "denied", "error": exc.message}


@router.post("/files/cluster")
def cluster_files(payload: dict | None = None):
    from app.tools.registry import register_all_tools
    from app.tools.registry import registry as tool_registry

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
            raise HTTPException(status_code=400, detail="k must be an integer.") from None
    for key in ("group_by", "cluster_by", "paths", "image_paths", "images", "limit", "metadata_weight"):
        if key in payload:
            args[key] = payload[key]
    group_by = str(payload.get("group_by") or "").strip().lower()
    image_grouping = group_by in {"image", "images", "scene", "people", "objects", "tags", "time", "location"}
    if group_by in {"image", "images"}:
        args["group_by"] = payload.get("cluster_by") or "auto"
    tool = tool_registry.get("image.cluster_images" if image_grouping else "file.cluster_by_content")
    return tool.execute(args, context)
