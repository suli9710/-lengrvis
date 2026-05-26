from __future__ import annotations

from fastapi import APIRouter

from app.llm.registry import get_effective_settings
from app.mcp import get_mcp_registry


router = APIRouter()


@router.get("/mcp/servers")
def list_servers() -> dict:
    settings = get_effective_settings()
    registry = get_mcp_registry()
    registry.load_from_settings(settings)
    servers = registry.list_servers()
    return {"servers": servers, "count": len(servers)}


@router.get("/mcp/tools")
async def list_tools() -> dict:
    registry = get_mcp_registry()
    tools = await registry.list_all_tools()
    return {"tools": tools, "count": len(tools)}


@router.get("/mcp/resources")
async def list_resources() -> dict:
    registry = get_mcp_registry()
    resources = await registry.list_all_resources()
    return {"resources": resources, "count": len(resources)}
