"""Minimal MCP (Model Context Protocol) HTTP client.

This intentionally avoids importing the official `mcp` SDK so the rest of the
backend stays runnable without optional dependencies. It speaks the JSON-RPC 2.0
shape used by the MCP spec for `tools/list` and `tools/call`. When a real MCP
server is configured the client streams responses; otherwise it returns the
captured error inline.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx


DEFAULT_TIMEOUT = 30
JSONRPC_VERSION = "2.0"


@dataclass(slots=True)
class MCPServerConfig:
    name: str
    url: str
    transport: str = "http"
    enabled: bool = True
    command: str = ""
    args: list[str] | None = None
    auth: dict[str, Any] | None = None


class MCPClient:
    def __init__(self, config: MCPServerConfig, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.config = config
        self.timeout = timeout
        self._tools_cache: list[dict[str, Any]] | None = None
        self._lock = asyncio.Lock()

    async def list_tools(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        unsupported = self._unsupported_transport_error()
        if unsupported:
            return []
        if self._tools_cache is not None and not force_refresh:
            return self._tools_cache
        async with self._lock:
            payload = {
                "jsonrpc": JSONRPC_VERSION,
                "id": "tools-list",
                "method": "tools/list",
                "params": {},
            }
            data = await self._post(payload)
            tools = data.get("result", {}).get("tools", []) or []
            normalized: list[dict[str, Any]] = []
            for entry in tools:
                if not isinstance(entry, dict):
                    continue
                normalized.append(
                    {
                        "name": str(entry.get("name") or entry.get("id") or ""),
                        "description": str(entry.get("description") or ""),
                        "input_schema": entry.get("inputSchema") or entry.get("input_schema") or {},
                    }
                )
            self._tools_cache = normalized
            return normalized

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        unsupported = self._unsupported_transport_error()
        if unsupported:
            return {"ok": False, "error": unsupported, "server": self.config.name}
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "id": f"call-{tool_name}",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
        data = await self._post(payload)
        if "error" in data:
            return {"ok": False, "error": data["error"].get("message", "MCP error"), "server": self.config.name}
        result = data.get("result", {})
        return {"ok": True, "result": result, "server": self.config.name}

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._auth_required():
            return {"error": {"message": "authentication required", "type": "auth_required"}}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(self.config.url, json=payload, headers={"Content-Type": "application/json"})
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                return {"error": {"message": f"transport error: {exc}", "type": "transport"}}
            except json.JSONDecodeError as exc:
                return {"error": {"message": f"invalid response: {exc}", "type": "decode"}}

    def status(self) -> dict[str, Any]:
        unsupported = self._unsupported_transport_error()
        if self._auth_required():
            state = "needs_auth"
        elif unsupported:
            state = "unsupported_transport"
        else:
            state = "configured"
        return {
            "name": self.config.name,
            "transport": self.config.transport,
            "url": self.config.url,
            "command": self.config.command,
            "enabled": self.config.enabled,
            "state": state,
            "error": unsupported,
            "auth_required": self._auth_required(),
            "tool_count": len(self._tools_cache or []),
        }

    async def list_resources(self) -> list[dict[str, Any]]:
        if self._unsupported_transport_error() or self._auth_required():
            return []
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "id": "resources-list",
            "method": "resources/list",
            "params": {},
        }
        data = await self._post(payload)
        resources = data.get("result", {}).get("resources", []) or []
        return [resource for resource in resources if isinstance(resource, dict)]

    def _unsupported_transport_error(self) -> str:
        transport = (self.config.transport or "http").casefold()
        if transport in {"http", "https"}:
            return "" if self.config.url else "http transport requires url"
        if transport in {"sse", "stdio"}:
            return f"{transport} transport is configured but not connected by the lightweight backend client yet"
        return f"unsupported MCP transport: {self.config.transport}"

    def _auth_required(self) -> bool:
        auth = self.config.auth or {}
        return bool(auth.get("required")) and not auth.get("token")
