"""MCP server registry & adapter to mavris ToolDefinition objects."""

from __future__ import annotations

import asyncio
from typing import Any

from app.config import AppSettings
from app.core.audit import record
from app.mcp.client import MCPClient, MCPServerConfig
from app.policy.risk import RiskLevel
from app.tools.schemas import ToolDefinition


class MCPRegistry:
    """Loads MCP server configs from AppSettings and exposes their tools as ToolDefinitions."""

    def __init__(self) -> None:
        self.clients: dict[str, MCPClient] = {}

    def load_from_settings(self, settings: AppSettings) -> None:
        self.clients.clear()
        for entry in settings.mcp_servers:
            if not entry.get("enabled", True):
                continue
            config = MCPServerConfig(
                name=str(entry.get("name") or "mcp"),
                url=str(entry.get("url") or ""),
                transport=str(entry.get("transport", "http")),
                enabled=bool(entry.get("enabled", True)),
                command=str(entry.get("command") or ""),
                args=list(entry.get("args") or []),
                auth=dict(entry.get("auth") or {}),
            )
            if not config.url and not config.command:
                continue
            self.clients[config.name] = MCPClient(config)
        record("mcp.registry_loaded", "MCPRegistry", {"servers": list(self.clients.keys())})

    def list_servers(self) -> list[dict[str, Any]]:
        return [client.status() for client in self.clients.values()]

    async def list_all_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for server_name, client in self.clients.items():
            try:
                discovered = await client.list_tools()
            except Exception as exc:  # noqa: BLE001
                record("mcp.list_failed", "MCPRegistry", {"server": server_name, "error": str(exc)})
                continue
            for tool in discovered:
                tools.append({"server": server_name, "transport": client.config.transport, **tool})
        return tools

    async def list_all_resources(self) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        for server_name, client in self.clients.items():
            try:
                discovered = await client.list_resources()
            except Exception as exc:  # noqa: BLE001
                record("mcp.resources_failed", "MCPRegistry", {"server": server_name, "error": str(exc)})
                continue
            for resource in discovered:
                resources.append({"server": server_name, "transport": client.config.transport, **resource})
        return resources

    async def adapt_to_tool_definitions(self) -> list[ToolDefinition]:
        adapted: list[ToolDefinition] = []
        all_tools = await self.list_all_tools()
        for tool in all_tools:
            server = tool["server"]
            name = f"mcp.{server}.{tool['name']}"
            adapted.append(
                ToolDefinition(
                    name=name,
                    description=tool.get("description") or name,
                    input_schema=tool.get("input_schema") or {},
                    output_schema={"type": "object"},
                    risk_level=RiskLevel.R4_FORBIDDEN_OR_HANDOFF,
                    agent_owner="SearchAgent",
                    supports_dry_run=False,
                    requires_authorized_path=False,
                    execute=_build_executor(self, server, tool["name"]),
                    search_hint="third-party MCP tool; requires explicit local trust configuration before execution",
                    capabilities=["mcp", "third_party"],
                    effects=["external_call"],
                    resource_kinds=["external_service"],
                    fast_path_eligible=False,
                    trust_tier="third_party",
                    sensitive_arg_keys=["authorization", "cookie", "password", "secret", "token"],
                    external_network=True,
                )
            )
        return adapted


def _build_executor(registry: "MCPRegistry", server: str, tool_name: str):
    def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        client = registry.clients.get(server)
        if client is None:
            return {"ok": False, "error": f"MCP server '{server}' not registered"}
        return asyncio.run(client.call_tool(tool_name, args))

    return execute


_registry: MCPRegistry | None = None


def get_mcp_registry() -> MCPRegistry:
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry
