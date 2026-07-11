from __future__ import annotations

from fastapi import APIRouter, Response

from app.llm.registry import get_effective_settings
from app.security.capability_manifest import build_capability_manifest
from app.tools.registry import registry as tool_registry

router = APIRouter()


@router.get("/security/capability-manifest")
def capability_manifest_status(response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return build_capability_manifest(
        settings=get_effective_settings(),
        tools=tool_registry.list(),
    )
