from __future__ import annotations

from fastapi import APIRouter

from app.services.skill_service import import_skill, list_installed_skills, refresh_runtime_registry

router = APIRouter()


@router.get("/skills")
def list_skills() -> dict:
    return list_installed_skills()


@router.post("/skills/import")
async def import_skill_package(payload: dict) -> dict:
    source_path = str(payload.get("path") or "").strip()
    permission_diff_reviewed = bool(payload.get("permission_diff_reviewed") or payload.get("permissionDiffReviewed"))
    return await import_skill(source_path, permission_diff_reviewed=permission_diff_reviewed)


@router.post("/skills/refresh")
async def refresh_skills() -> dict:
    return await refresh_runtime_registry()
