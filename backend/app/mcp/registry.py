"""MCP server registry & adapter to Lengrvis ToolDefinition objects."""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any

from app.config import AppSettings
from app.core.audit import record
from app.mcp.client import MCPClient, MCPServerConfig
from app.policy.risk import RiskLevel
from app.security.capability_manifest import (
    CapabilityManifestError,
    assert_capability_allowed,
    canonical_content_hash,
    mcp_server_capability_payload,
    observe_capability,
)
from app.tools.schemas import ToolDefinition

DEFAULT_MCP_EXECUTOR_TIMEOUT_SECONDS = 30.0


class MCPRegistry:
    """Loads MCP server configs from AppSettings and exposes their tools as ToolDefinitions."""

    def __init__(self) -> None:
        self.clients: dict[str, MCPClient] = {}
        self.require_owner_policy = False

    def load_from_settings(self, settings: AppSettings) -> None:
        self.clients.clear()
        self.require_owner_policy = bool(getattr(settings, "mcp_require_owner_policy", False))
        for entry in settings.mcp_servers:
            if not entry.get("enabled", True):
                continue
            if self.require_owner_policy:
                _validate_mcp_owner_policy(entry)
            config = MCPServerConfig(
                name=str(entry.get("name") or "mcp"),
                url=str(entry.get("url") or ""),
                transport=str(entry.get("transport", "http")),
                enabled=bool(entry.get("enabled", True)),
                command=str(entry.get("command") or ""),
                args=list(entry.get("args") or []),
                auth=dict(entry.get("auth") or {}),
                owner=str(entry.get("owner") or ""),
                policy_id=str(entry.get("policy_id") or entry.get("policyId") or ""),
                allowed_tools=_mcp_string_list(entry.get("allowed_tools") or entry.get("allowedTools")),
            )
            if not config.url and not config.command:
                continue
            capability = observe_capability(
                "mcp_server",
                config.name,
                mcp_server_capability_payload(config),
                version="1",
                origin="runtime_config",
            )
            try:
                assert_capability_allowed(
                    capability.kind,
                    capability.capability_id,
                    content_hash=capability.content_hash,
                )
            except CapabilityManifestError:
                continue
            self.clients[config.name] = MCPClient(config)
        record(
            "mcp.registry_loaded",
            "MCPRegistry",
            {
                "server_count": len(self.clients),
                "server_config_hashes": [
                    canonical_content_hash(mcp_server_capability_payload(client.config))
                    for client in self.clients.values()
                ],
            },
        )

    def list_servers(self) -> list[dict[str, Any]]:
        return [client.status() for client in self.clients.values()]

    async def list_all_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for server_name, client in self.clients.items():
            try:
                discovered = await client.list_tools()
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
                record(
                    "mcp.list_failed",
                    "MCPRegistry",
                    {
                        "server_config_hash": canonical_content_hash(
                            mcp_server_capability_payload(client.config)
                        ),
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            for tool in discovered:
                tools.append({"server": server_name, "transport": client.config.transport, **tool})
        return tools

    async def list_all_resources(self) -> list[dict[str, Any]]:
        resources: list[dict[str, Any]] = []
        for server_name, client in self.clients.items():
            try:
                discovered = await client.list_resources()
            except Exception as exc:  # noqa: BLE001 - broad-exception-boundary
                record(
                    "mcp.resources_failed",
                    "MCPRegistry",
                    {
                        "server_config_hash": canonical_content_hash(
                            mcp_server_capability_payload(client.config)
                        ),
                        "error_type": type(exc).__name__,
                    },
                )
                continue
            for resource in discovered:
                resources.append({"server": server_name, "transport": client.config.transport, **resource})
        return resources

    async def adapt_to_tool_definitions(self) -> list[ToolDefinition]:
        adapted: list[ToolDefinition] = []
        all_tools = await self.list_all_tools()
        for tool in all_tools:
            server = tool["server"]
            client = self.clients.get(server)
            if client is None:
                continue
            if client.config.allowed_tools and tool["name"] not in client.config.allowed_tools:
                record(
                    "mcp.tool_not_approved",
                    "MCPRegistry",
                    {
                        "server_config_hash": canonical_content_hash(
                            mcp_server_capability_payload(client.config)
                        ),
                        "tool_id_hash": canonical_content_hash({"id": tool["name"]}),
                        "policy_id_hash": canonical_content_hash({"id": client.config.policy_id}),
                    },
                )
                continue
            name = f"mcp.{server}.{tool['name']}"
            tool_version = canonical_content_hash(
                {
                    "server": mcp_server_capability_payload(client.config),
                    "name": tool.get("name"),
                    "description": tool.get("description"),
                    "input_schema": tool.get("input_schema") or {},
                }
            )
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
                    origin=f"mcp:{server}",
                    tool_version=tool_version,
                )
            )
        return adapted


def _validate_mcp_owner_policy(entry: dict[str, Any]) -> None:
    name = str(entry.get("name") or entry.get("id") or "mcp")
    owner = str(entry.get("owner") or "").strip()
    policy_id = str(entry.get("policy_id") or entry.get("policyId") or "").strip()
    allowed_tools = _mcp_string_list(entry.get("allowed_tools") or entry.get("allowedTools"))
    if not owner or owner.upper() == "TBD":
        raise ValueError(f"MCP server '{name}' requires an owner in release profile.")
    if not policy_id or policy_id.upper() == "TBD":
        raise ValueError(f"MCP server '{name}' requires an owner-approved policy_id in release profile.")
    if not allowed_tools or not all(str(tool).strip() for tool in allowed_tools):
        raise ValueError(f"MCP server '{name}' requires non-empty allowed_tools in release profile.")


def _mcp_string_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _build_executor(registry: MCPRegistry, server: str, tool_name: str):
    def execute(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        client = registry.clients.get(server)
        if client is None:
            return {"ok": False, "error": f"MCP server '{server}' not registered"}
        try:
            assert_capability_allowed(
                "mcp_server",
                server,
                payload=mcp_server_capability_payload(client.config),
            )
        except CapabilityManifestError as exc:
            return {"ok": False, "error": exc.message, "server": server}
        return _run_mcp_call(client, tool_name, args)

    return execute


async def _with_timeout(coro, timeout_seconds: float | None) -> Any:
    if timeout_seconds is None or timeout_seconds <= 0:
        return await coro
    return await asyncio.wait_for(coro, timeout=timeout_seconds)


def _run_mcp_call(client: MCPClient, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    timeout = float(getattr(client, "timeout", DEFAULT_MCP_EXECUTOR_TIMEOUT_SECONDS) or 0)
    server = str(getattr(getattr(client, "config", None), "name", "") or "")
    coro = _with_timeout(client.call_tool(tool_name, args), timeout)
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(asyncio.run, coro)
        try:
            guard_timeout = None if timeout <= 0 else timeout + 1
            return future.result(timeout=guard_timeout)
        finally:
            if not future.done():
                future.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
    except TimeoutError:
        return {"ok": False, "error": "MCP tool call timed out.", "server": server}
    except Exception as exc:  # noqa: BLE001 - broad-exception-boundary: third-party MCP tools should fail inline.
        return {"ok": False, "error": f"MCP tool call failed: {exc}", "server": server}


_registry: MCPRegistry | None = None


def get_mcp_registry() -> MCPRegistry:
    global _registry
    if _registry is None:
        _registry = MCPRegistry()
    return _registry
