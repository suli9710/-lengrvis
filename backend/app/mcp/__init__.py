"""MCP integration package: HTTP-only minimal client + ToolDefinition adapter."""

from app.mcp.client import MCPClient, MCPServerConfig
from app.mcp.registry import MCPRegistry, get_mcp_registry

__all__ = ["MCPClient", "MCPServerConfig", "MCPRegistry", "get_mcp_registry"]
