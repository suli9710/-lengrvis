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

from app.core.audit import record
from app.core.outbound_url import pin_outbound_http_url

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
    owner: str = ""
    policy_id: str = ""
    allowed_tools: list[str] | None = None


class MCPClient:
    def __init__(self, config: MCPServerConfig, *, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.config = config
        self.timeout = timeout
        self._tools_cache: list[dict[str, Any]] | None = None
        self._tools_cache_error = ""
        self._lock = asyncio.Lock()

    async def list_tools(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        unsupported = self._unsupported_transport_error()
        if unsupported:
            self._tools_cache_error = unsupported
            self._tools_cache = []
            return []
        if self._auth_required():
            self._tools_cache_error = "authentication required"
            self._tools_cache = []
            return []
        # P1-12 fix: Move cache check inside the lock to prevent race conditions.
        # The old code checked self._tools_cache outside self._lock, allowing
        # multiple concurrent callers to bypass the cache and make duplicate
        # HTTP requests (and IP lock失效).
        async with self._lock:
            if self._tools_cache is not None and not force_refresh:
                return self._tools_cache
            self._tools_cache_error = ""
            payload = {
                "jsonrpc": JSONRPC_VERSION,
                "id": "tools-list",
                "method": "tools/list",
                "params": {},
            }
            data = await self._post(payload)
            if "error" in data:
                self._tools_cache_error = str(data["error"].get("message") or "MCP tools/list failed")
                return []
            tools = data.get("result", {}).get("tools", []) or []
            normalized: list[dict[str, Any]] = []
            for entry in tools:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or entry.get("id") or "")
                if not name:
                    continue
                normalized.append(
                    {
                        "name": name,
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
        if self._auth_required():
            return {"ok": False, "error": "authentication required", "server": self.config.name}
        schema, schema_error = await self._input_schema_for_tool(tool_name)
        if schema_error:
            record(
                "mcp.tool_call_blocked",
                "MCPClient",
                {"server": self.config.name, "tool": tool_name, "reason": schema_error},
            )
            return {"ok": False, "error": schema_error, "server": self.config.name}
        args = {} if arguments is None else arguments
        validation_error = _validate_tool_arguments(args, schema or {})
        if validation_error:
            record(
                "mcp.tool_call_blocked",
                "MCPClient",
                {"server": self.config.name, "tool": tool_name, "reason": validation_error},
            )
            return {"ok": False, "error": validation_error, "server": self.config.name}
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "id": f"call-{tool_name}",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": args,
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
        try:
            # Connect-time IP pin (DNS-rebinding TOCTOU): we connect to the IP
            # that passed validation, not whatever the name resolves to later.
            pinned = pin_outbound_http_url(self.config.url, allow_private=False)
        except ValueError as exc:
            return {"error": {"message": str(exc), "type": "invalid_url"}}
        headers = {"Content-Type": "application/json", **pinned.headers}
        token = (self.config.auth or {}).get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            try:
                response = await client.post(
                    pinned.url, json=payload, headers=headers, extensions=dict(pinned.extensions)
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError as exc:
                return {"error": {"message": f"transport error: {exc}", "type": "transport"}}
            except json.JSONDecodeError as exc:
                return {"error": {"message": f"invalid response: {exc}", "type": "decode"}}

    async def _input_schema_for_tool(self, tool_name: str) -> tuple[dict[str, Any] | None, str]:
        tools = await self.list_tools()
        if self._tools_cache_error:
            return None, f"MCP tool schema discovery failed: {self._tools_cache_error}"
        for tool in tools:
            if tool.get("name") == tool_name:
                schema = tool.get("input_schema") or {}
                if not isinstance(schema, dict):
                    return None, f"MCP tool '{tool_name}' has an invalid input_schema"
                return schema, ""
        return None, f"unknown MCP tool '{tool_name}' was not advertised by server '{self.config.name}'"

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
            "owner": self.config.owner,
            "policy_id": self.config.policy_id,
            "allowed_tools": list(self.config.allowed_tools or []),
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


def _validate_tool_arguments(arguments: Any, schema: dict[str, Any]) -> str:
    if not isinstance(arguments, dict):
        return "MCP tool arguments must be a JSON object"
    if not schema:
        return ""
    if not isinstance(schema, dict):
        return "MCP tool input_schema must be a JSON schema object"
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError, ValidationError
    except ImportError:  # pragma: no cover - jsonschema may be absent in minimal installs.
        return _validate_tool_arguments_lightweight(arguments, schema)
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(arguments)
    except SchemaError as exc:
        return f"MCP tool input_schema is invalid: {exc.message}"
    except ValidationError as exc:
        return f"MCP tool arguments did not match input_schema: {exc.message}"
    return ""


def _validate_tool_arguments_lightweight(arguments: dict[str, Any], schema: dict[str, Any]) -> str:
    expected_type = schema.get("type")
    if expected_type and not _matches_json_schema_type(arguments, expected_type):
        return "MCP tool arguments did not match input_schema: arguments must be an object"
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    for field in required:
        if isinstance(field, str) and field not in arguments:
            return f"MCP tool arguments did not match input_schema: {field!r} is required"
    if schema.get("additionalProperties") is False:
        extra = sorted(set(arguments) - set(properties))
        if extra:
            return f"MCP tool arguments did not match input_schema: unexpected fields: {', '.join(extra)}"
    for field, field_schema in properties.items():
        if field not in arguments or not isinstance(field_schema, dict):
            continue
        field_type = field_schema.get("type")
        if field_type and not _matches_json_schema_type(arguments[field], field_type):
            return f"MCP tool arguments did not match input_schema: {field!r} has the wrong type"
        enum = field_schema.get("enum")
        if isinstance(enum, list) and arguments[field] not in enum:
            return f"MCP tool arguments did not match input_schema: {field!r} is not an allowed value"
    return ""


def _matches_json_schema_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_json_schema_type(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True
