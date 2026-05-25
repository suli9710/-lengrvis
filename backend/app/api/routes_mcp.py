from __future__ import annotations

from fastapi import APIRouter

from app.llm.registry import get_effective_settings
from app.mcp import get_mcp_registry


router = APIRouter()


@router.get("/mcp/servers")
def list_servers() -> dict:
    settings = get_effective_settings()
    return {"servers": settings.mcp_servers, "count": len(settings.mcp_servers)}


@router.get("/mcp/tools")
async def list_tools() -> dict:
    registry = get_mcp_registry()
    tools = await registry.list_all_tools()
    return {"tools": tools, "count": len(tools)}
