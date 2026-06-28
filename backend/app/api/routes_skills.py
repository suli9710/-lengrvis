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
    return await import_skill(source_path)


@router.post("/skills/refresh")
async def refresh_skills() -> dict:
    return await refresh_runtime_registry()
