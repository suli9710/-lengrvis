"""Fail-closed MCP Streamable HTTP client.

The official MCP SDK remains optional so the backend can run in a minimal
installation.  The wire client nevertheless implements the protocol lifecycle
needed by a production HTTP integration: initialization and capability
negotiation, protocol/session headers, session renewal, strict JSON-RPC
envelope validation, and JSON/SSE response decoding.  Third-party tools are
still approval-gated by :mod:`app.mcp.registry`.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import math
import os
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.audit import record
from app.core.outbound_url import pin_outbound_http_url
from app.mcp.http_stream import post_streaming_http
from app.mcp.protocol import (
    JSONRPC_VERSION,
    _canonical_mcp_resource,
    _decode_json_messages,
    _decode_json_response,
    _next_page_cursor,
    _select_jsonrpc_response,
    _valid_session_id,
    _validate_json_schema,
    _validate_jsonrpc_envelope,
    _validate_tool_arguments,
)
from app.mcp.stdio_transport import MCPStdioTransport, MCPStdioTransportError
from app.security.capability_manifest import (
    CapabilityManifestError,
    assert_capability_allowed,
    mcp_server_capability_payload,
)
from app.security.execution_isolation import release_profile_active

DEFAULT_TIMEOUT = 30
MAX_PAGINATION_PAGES = 100
MAX_PROGRESS_NOTIFICATIONS_PER_SECOND = 20
MCP_PROTOCOL_VERSION = "2025-11-25"
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


@dataclass(slots=True)
class MCPServerConfig:
    name: str
    url: str
    transport: str = "http"
    enabled: bool = True
    command: str = ""
    args: list[str] | None = None
    env: dict[str, str] | None = None
    inherit_env: list[str] | None = None
    auth: dict[str, Any] | None = None
    owner: str = ""
    policy_id: str = ""
    allowed_tools: list[str] | None = None
    protocol_version: str = MCP_PROTOCOL_VERSION
    strict_lifecycle: bool = True
    client_name: str = "Lengrvis"
    client_version: str = "0.1.2"


@dataclass(slots=True)
class _ActiveRequest:
    request_id: str | int
    progress_token: str | int | None = None
    progress_callback: Callable[[dict[str, Any]], Any] | None = None
    last_progress: float | None = None
    rate_window_started: float = field(default_factory=time.monotonic)
    rate_window_count: int = 0


class MCPClient:
    def __init__(
        self,
        config: MCPServerConfig,
        *,
        timeout: int = DEFAULT_TIMEOUT,
        url_pinner: Callable[[str], Any] | None = None,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self._tools_cache: list[dict[str, Any]] | None = None
        self._tools_cache_error = ""
        self._lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._initialized = False
        self._initialization_error = ""
        self._session_id: str | None = None
        self._negotiated_protocol_version: str | None = None
        self._server_capabilities: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}
        self._last_response_headers: dict[str, str] = {}
        self._request_counter = 0
        self._active_requests: dict[str | int, _ActiveRequest] = {}
        self._stdio_transport: MCPStdioTransport | None = None
        self._url_pinner = url_pinner

    async def list_tools(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        try:
            return await self._list_tools_impl(force_refresh=force_refresh)
        finally:
            await self._finish_stdio_operation()

    async def _list_tools_impl(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        capability_error = self._capability_error()
        if capability_error:
            self._tools_cache_error = capability_error
            self._tools_cache = []
            return []
        unsupported = self._unsupported_transport_error()
        if unsupported:
            self._tools_cache_error = unsupported
            self._tools_cache = []
            return []
        auth_error = self._auth_error()
        if auth_error:
            self._tools_cache_error = auth_error
            self._tools_cache = []
            return []
        lifecycle_error = await self._ensure_initialized()
        if lifecycle_error:
            self._tools_cache_error = lifecycle_error
            self._tools_cache = []
            return []
        if self.config.strict_lifecycle and not isinstance(self._server_capabilities.get("tools"), dict):
            self._tools_cache_error = "MCP server did not advertise the tools capability"
            self._tools_cache = []
            return []

        # Keep cache inspection under the lock: concurrent callers must not
        # issue duplicate discovery requests or defeat the outbound IP pin.
        async with self._lock:
            if self._tools_cache is not None and not force_refresh:
                return self._tools_cache
            self._tools_cache_error = ""
            normalized: list[dict[str, Any]] = []
            seen_tool_names: set[str] = set()
            cursor: str | None = None
            seen_cursors: set[str] = set()
            for _page in range(MAX_PAGINATION_PAGES):
                payload = self._request_payload("tools/list", {"cursor": cursor} if cursor else {})
                data = await self._rpc(payload)
                if "error" in data:
                    self._tools_cache_error = str(data["error"].get("message") or "MCP tools/list failed")
                    return []
                result = data.get("result")
                if not isinstance(result, dict):
                    self._tools_cache_error = "MCP tools/list returned an invalid result"
                    return []
                tools = result.get("tools", []) or []
                if not isinstance(tools, list):
                    self._tools_cache_error = "MCP tools/list returned an invalid tools array"
                    return []
                for entry in tools:
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("name") or entry.get("id") or "")
                    if not name:
                        continue
                    if name in seen_tool_names:
                        self._tools_cache_error = f"MCP tools/list advertised duplicate tool name: {name}"
                        return []
                    seen_tool_names.add(name)
                    normalized.append(
                        {
                            "name": name,
                            "description": str(entry.get("description") or ""),
                            "input_schema": entry.get("inputSchema") or entry.get("input_schema") or {},
                            "output_schema": entry.get("outputSchema") or entry.get("output_schema") or {},
                        }
                    )
                next_cursor, cursor_error = _next_page_cursor(result, seen_cursors)
                if cursor_error:
                    self._tools_cache_error = f"MCP tools/list {cursor_error}"
                    return []
                if next_cursor is None:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                self._tools_cache_error = "MCP tools/list exceeded the pagination limit"
                return []
            self._tools_cache = normalized
            return normalized

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._call_tool_impl(
                tool_name,
                arguments,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
        finally:
            await self._finish_stdio_operation()

    async def _call_tool_impl(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        progress_callback: Callable[[dict[str, Any]], Any] | None,
        cancel_event: asyncio.Event | None,
    ) -> dict[str, Any]:
        capability_error = self._capability_error()
        if capability_error:
            return {"ok": False, "error": capability_error, "server": self.config.name}
        unsupported = self._unsupported_transport_error()
        if unsupported:
            return {"ok": False, "error": unsupported, "server": self.config.name}
        auth_error = self._auth_error()
        if auth_error:
            return {"ok": False, "error": auth_error, "server": self.config.name}
        lifecycle_error = await self._ensure_initialized()
        if lifecycle_error:
            return {"ok": False, "error": lifecycle_error, "server": self.config.name}

        schema, output_schema, schema_error = await self._schemas_for_tool(tool_name)
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

        if cancel_event is not None and cancel_event.is_set():
            return {
                "ok": False,
                "cancelled": True,
                "error": "MCP tool call was cancelled before it was issued.",
                "server": self.config.name,
            }
        params: dict[str, Any] = {"name": tool_name, "arguments": args}
        progress_token: str | None = None
        if progress_callback is not None:
            progress_token = f"lengrvis-progress-{secrets.token_urlsafe(18)}"
            params["_meta"] = {"progressToken": progress_token}
        payload = self._request_payload("tools/call", params)
        request_id = payload["id"]
        self._active_requests[request_id] = _ActiveRequest(
            request_id=request_id,
            progress_token=progress_token,
            progress_callback=progress_callback,
        )
        try:
            data = await self._rpc_with_cancellation(payload, cancel_event=cancel_event)
        finally:
            self._active_requests.pop(request_id, None)
        if data.get("cancelled") is True:
            return {
                "ok": False,
                "cancelled": True,
                "error": "MCP tool call was cancelled.",
                "server": self.config.name,
            }
        if "error" in data:
            return {"ok": False, "error": data["error"].get("message", "MCP error"), "server": self.config.name}
        result = data.get("result", {})
        if not isinstance(result, dict):
            return {"ok": False, "error": "MCP tools/call returned an invalid result", "server": self.config.name}
        if result.get("isError") is True:
            return {
                "ok": False,
                "error": "MCP tool reported an execution error",
                "result": result,
                "server": self.config.name,
            }
        if output_schema:
            structured = result.get("structuredContent")
            if structured is None:
                return {
                    "ok": False,
                    "error": "MCP tool result omitted structuredContent required by outputSchema",
                    "server": self.config.name,
                }
            output_error = _validate_json_schema(structured, output_schema, label="outputSchema")
            if output_error:
                record(
                    "mcp.tool_result_blocked",
                    "MCPClient",
                    {"server": self.config.name, "tool": tool_name, "reason": output_error},
                )
                return {"ok": False, "error": output_error, "server": self.config.name}
        return {"ok": True, "result": result, "server": self.config.name}

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._is_stdio_transport():
            return await self._stdio_post(payload)
        auth_error = self._auth_error()
        if auth_error:
            return {"error": {"message": auth_error, "type": "auth_required"}}
        try:
            # Connect-time IP pin (DNS-rebinding TOCTOU): connect to the IP
            # that passed validation, not whatever the name resolves to later.
            pinned = self._pin_url(self.config.url)
        except ValueError as exc:
            return {"error": {"message": str(exc), "type": "invalid_url"}}

        method = str(payload.get("method") or "")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **pinned.headers,
        }
        if method != "initialize":
            headers["MCP-Protocol-Version"] = self._negotiated_protocol_version or self.config.protocol_version
            if self._session_id:
                headers["MCP-Session-Id"] = self._session_id
        token = self._auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        active = self._active_requests.get(payload.get("id"))
        # A tools/call response may use SSE even when the caller did not ask
        # for progress updates.  Active calls therefore always take the
        # streaming path so a graceful close can be resumed with GET.
        if active is not None:
            streamed = await post_streaming_http(
                pinned=pinned,
                payload=payload,
                headers=headers,
                timeout=self.timeout,
                session_id=self._session_id,
                protocol_version=self._negotiated_protocol_version or self.config.protocol_version,
                auth_token=token,
                handle_notification=self._handle_notification,
                http_client_factory=httpx.AsyncClient,
            )
            self._last_response_headers = streamed.response_headers
            if streamed.session_expired:
                self._reset_lifecycle()
            return streamed.message

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
            try:
                response = await client.post(
                    pinned.url, json=payload, headers=headers, extensions=dict(pinned.extensions)
                )
                self._last_response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                if response.status_code == 404 and self._session_id and method != "initialize":
                    self._reset_lifecycle()
                    return {"error": {"message": "MCP session expired", "type": "session_expired", "status": 404}}
                if response.status_code in {202, 204} and "id" not in payload:
                    return {}
                if response.status_code >= 400:
                    try:
                        body = _decode_json_response(response)
                    except ValueError:
                        body = {}
                    if isinstance(body, dict) and isinstance(body.get("error"), dict):
                        return body
                    return {
                        "error": {
                            "message": (
                                f"MCP transport HTTP error {response.status_code}"
                                if response.status_code >= 500
                                else f"MCP HTTP error {response.status_code}"
                            ),
                            "type": "transport" if response.status_code >= 500 else "http",
                            "status": response.status_code,
                        }
                    }
                messages = _decode_json_messages(response)
                for message in messages:
                    if isinstance(message.get("method"), str) and message.get("id") is None:
                        await self._handle_notification(message)
                data = _select_jsonrpc_response(messages, expected_id=payload.get("id"))
                if method == "initialize":
                    session_id = self._last_response_headers.get("mcp-session-id")
                    if session_id:
                        if not _valid_session_id(session_id):
                            return {
                                "error": {
                                    "message": "MCP server returned an invalid session id",
                                    "type": "protocol",
                                }
                            }
                        self._session_id = session_id
                return data
            except httpx.HTTPError as exc:
                return {"error": {"message": f"transport error: {exc}", "type": "transport"}}
            except (ValueError, UnicodeError) as exc:
                return {"error": {"message": f"invalid response: {exc}", "type": "decode"}}

    async def _input_schema_for_tool(self, tool_name: str) -> tuple[dict[str, Any] | None, str]:
        schema, _output, error = await self._schemas_for_tool(tool_name)
        return schema, error

    async def _schemas_for_tool(self, tool_name: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
        tools = await self._list_tools_impl()
        if self._tools_cache_error:
            return None, None, f"MCP tool schema discovery failed: {self._tools_cache_error}"
        for tool in tools:
            if tool.get("name") == tool_name:
                schema = tool.get("input_schema") or {}
                if not isinstance(schema, dict):
                    return None, None, f"MCP tool '{tool_name}' has an invalid input_schema"
                output_schema = tool.get("output_schema") or {}
                if output_schema and not isinstance(output_schema, dict):
                    return None, None, f"MCP tool '{tool_name}' has an invalid outputSchema"
                return schema, output_schema or None, ""
        return None, None, f"unknown MCP tool '{tool_name}' was not advertised by server '{self.config.name}'"

    def status(self) -> dict[str, Any]:
        unsupported = self._unsupported_transport_error()
        auth_error = self._auth_error()
        if auth_error:
            state = "needs_auth"
        elif unsupported:
            state = "unsupported_transport"
        elif self._initialization_error:
            state = "lifecycle_error"
        elif self._initialized:
            state = "ready"
        else:
            state = "configured"
        capability = mcp_server_capability_payload(self.config)
        return {
            "name": self.config.name,
            "transport": self.config.transport,
            "url": capability["endpoint"],
            "command": capability["command"],
            "enabled": self.config.enabled,
            "state": state,
            "error": unsupported or auth_error or self._initialization_error,
            "auth_required": bool(auth_error),
            "tool_count": len(self._tools_cache or []),
            "owner": self.config.owner,
            "policy_id": self.config.policy_id,
            "allowed_tools": list(self.config.allowed_tools or []),
            "protocol_version": self._negotiated_protocol_version or self.config.protocol_version,
            "initialized": self._initialized,
            "server_capabilities": sorted(self._server_capabilities),
            "server_info": {
                "name": str(self._server_info.get("name") or ""),
                "version": str(self._server_info.get("version") or ""),
            },
        }

    async def list_resources(self) -> list[dict[str, Any]]:
        try:
            return await self._list_resources_impl()
        finally:
            await self._finish_stdio_operation()

    async def _list_resources_impl(self) -> list[dict[str, Any]]:
        if self._capability_error():
            return []
        if self._unsupported_transport_error() or self._auth_error():
            return []
        lifecycle_error = await self._ensure_initialized()
        if lifecycle_error or (
            self.config.strict_lifecycle and not isinstance(self._server_capabilities.get("resources"), dict)
        ):
            return []
        collected: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(MAX_PAGINATION_PAGES):
            payload = self._request_payload("resources/list", {"cursor": cursor} if cursor else {})
            data = await self._rpc(payload)
            result = data.get("result")
            if "error" in data or not isinstance(result, dict):
                return []
            resources = result.get("resources", []) or []
            if not isinstance(resources, list):
                return []
            collected.extend(resource for resource in resources if isinstance(resource, dict))
            next_cursor, cursor_error = _next_page_cursor(result, seen_cursors)
            if cursor_error:
                return []
            if next_cursor is None:
                return collected
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return []

    async def close(self) -> None:
        """Best-effort graceful shutdown for stateful Streamable HTTP sessions."""
        if self._is_stdio_transport():
            await self._finish_stdio_operation()
            return
        if not self._session_id or self._unsupported_transport_error():
            self._reset_lifecycle()
            return
        try:
            await self._delete_session()
        finally:
            self._reset_lifecycle()

    async def _ensure_initialized(self) -> str:
        if not self.config.strict_lifecycle:
            return ""
        if self._initialized:
            return ""
        async with self._lifecycle_lock:
            if self._initialized:
                return ""
            self._initialization_error = ""
            payload = self._request_payload(
                "initialize",
                {
                    "protocolVersion": self.config.protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": self.config.client_name, "version": self.config.client_version},
                },
            )
            data = await self._post(payload)
            internal_error = data.get("error") if isinstance(data, dict) else None
            if isinstance(internal_error, dict) and internal_error.get("type"):
                message = str(internal_error.get("message") or "transport error")
                self._initialization_error = f"MCP initialize failed: {message}"
                return self._initialization_error
            error = _validate_jsonrpc_envelope(data, expected_id=payload["id"])
            if error:
                self._initialization_error = f"MCP initialize failed: {error}"
                return self._initialization_error
            result = data.get("result")
            if not isinstance(result, dict):
                self._initialization_error = "MCP initialize returned an invalid result"
                return self._initialization_error
            negotiated = result.get("protocolVersion")
            if not isinstance(negotiated, str) or not negotiated.strip():
                self._initialization_error = "MCP initialize omitted protocolVersion"
                return self._initialization_error
            if negotiated != self.config.protocol_version:
                self._initialization_error = f"MCP server negotiated unsupported protocol version: {negotiated}"
                return self._initialization_error
            capabilities = result.get("capabilities") or {}
            if not isinstance(capabilities, dict):
                self._initialization_error = "MCP initialize returned invalid capabilities"
                return self._initialization_error
            self._negotiated_protocol_version = negotiated
            self._server_capabilities = capabilities
            self._server_info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
            initialized = {"jsonrpc": JSONRPC_VERSION, "method": "notifications/initialized"}
            notification_result = await self._post(initialized)
            if notification_result.get("error"):
                self._initialization_error = "MCP initialized notification was rejected"
                return self._initialization_error
            self._initialized = True
            return ""

    def _request_payload(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._request_counter += 1
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": f"lengrvis-{self._request_counter}-{secrets.token_hex(4)}",
            "method": method,
            "params": params or {},
        }

    async def _rpc(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._post(payload)
        if data.get("error", {}).get("type") == "session_expired":
            lifecycle_error = await self._ensure_initialized()
            if lifecycle_error:
                return {"error": {"message": lifecycle_error}}
            data = await self._post(payload)
        envelope_error = _validate_jsonrpc_envelope(data, expected_id=payload.get("id"))
        if envelope_error:
            return {"error": {"message": envelope_error}}
        return data

    async def _rpc_with_cancellation(
        self,
        payload: dict[str, Any],
        *,
        cancel_event: asyncio.Event | None,
    ) -> dict[str, Any]:
        if cancel_event is None:
            return await self._rpc(payload)
        rpc_task = asyncio.create_task(self._rpc(payload))
        cancel_task = asyncio.create_task(cancel_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {rpc_task, cancel_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if rpc_task in done:
                return await rpc_task
            await self._send_cancellation(payload["id"], reason="User requested cancellation")
            rpc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await rpc_task
            return {"cancelled": True}
        finally:
            if not cancel_task.done():
                cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)

    async def _send_cancellation(self, request_id: str | int, *, reason: str) -> None:
        if request_id not in self._active_requests:
            return
        notification = {
            "jsonrpc": JSONRPC_VERSION,
            "method": "notifications/cancelled",
            "params": {"requestId": request_id, "reason": reason[:512]},
        }
        await self._post(notification)

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        if message.get("jsonrpc") != JSONRPC_VERSION:
            return
        if message.get("method") != "notifications/progress":
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        token = params.get("progressToken")
        active = next(
            (
                item
                for item in self._active_requests.values()
                if item.progress_token is not None and item.progress_token == token
            ),
            None,
        )
        if active is None or active.progress_callback is None:
            return
        raw_progress = params.get("progress")
        if isinstance(raw_progress, bool) or not isinstance(raw_progress, int | float):
            return
        progress = float(raw_progress)
        if not math.isfinite(progress):
            return
        if active.last_progress is not None and progress <= active.last_progress:
            return
        raw_total = params.get("total")
        total: float | None = None
        if raw_total is not None:
            if isinstance(raw_total, bool) or not isinstance(raw_total, int | float):
                return
            total = float(raw_total)
            if not math.isfinite(total):
                return
        now = time.monotonic()
        if now - active.rate_window_started >= 1.0:
            active.rate_window_started = now
            active.rate_window_count = 0
        if active.rate_window_count >= MAX_PROGRESS_NOTIFICATIONS_PER_SECOND:
            return
        active.rate_window_count += 1
        active.last_progress = progress
        update: dict[str, Any] = {"progress": progress}
        if total is not None:
            update["total"] = total
        if isinstance(params.get("message"), str):
            update["message"] = params["message"][:1024]
        callback_result = active.progress_callback(update)
        if inspect.isawaitable(callback_result):
            await callback_result

    async def _stdio_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self._stdio_transport is None:
            self._stdio_transport = MCPStdioTransport(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env,
                inherit_env=self.config.inherit_env,
                timeout=float(self.timeout),
                on_notification=self._handle_notification,
            )
        try:
            return await self._stdio_transport.request(payload)
        except MCPStdioTransportError as exc:
            return {"error": {"message": str(exc), "type": "transport"}}

    async def _finish_stdio_operation(self) -> None:
        transport = self._stdio_transport
        if transport is None:
            return
        self._stdio_transport = None
        error = self._initialization_error
        try:
            await transport.close()
        finally:
            self._reset_lifecycle(error)

    async def _delete_session(self) -> None:
        try:
            pinned = self._pin_url(self.config.url)
            headers = {
                **pinned.headers,
                "MCP-Session-Id": self._session_id or "",
                "MCP-Protocol-Version": self._negotiated_protocol_version or self.config.protocol_version,
            }
            token = self._auth_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as client:
                await client.delete(pinned.url, headers=headers, extensions=dict(pinned.extensions))
        except (httpx.HTTPError, ValueError):
            return

    def _reset_lifecycle(self, error: str = "") -> None:
        self._initialized = False
        self._initialization_error = error
        self._session_id = None
        self._negotiated_protocol_version = None
        self._server_capabilities = {}
        self._server_info = {}

    def _unsupported_transport_error(self) -> str:
        transport = (self.config.transport or "http").casefold()
        if transport in {"http", "https"}:
            return "" if self.config.url else "http transport requires url"
        if transport == "stdio":
            if not self.config.command:
                return "stdio transport requires command"
            if release_profile_active():
                return (
                    "stdio MCP subprocess transport is disabled in release profiles until it runs "
                    "inside the trusted Windows isolation host"
                )
            return ""
        if transport == "sse":
            return "legacy sse transport is configured but not connected by the MCP client"
        return f"unsupported MCP transport: {self.config.transport}"

    def _auth_required(self) -> bool:
        return bool(self._auth_error())

    def _auth_error(self) -> str:
        if self._is_stdio_transport():
            return ""
        auth = self.config.auth or {}
        token = self._auth_token()
        if bool(auth.get("required")) and not token:
            return "authentication required"
        if not token:
            return ""
        resource = str(auth.get("resource") or auth.get("audience") or "").strip()
        if not resource:
            return "MCP bearer token requires an explicit resource binding"
        try:
            expected = _canonical_mcp_resource(self.config.url)
            actual = _canonical_mcp_resource(resource)
        except ValueError:
            return "MCP bearer token resource binding is invalid"
        if actual != expected:
            return "MCP bearer token resource does not match the configured MCP endpoint"
        return ""

    def _auth_token(self) -> str:
        auth = self.config.auth or {}
        token = str(auth.get("token") or "").strip()
        if token:
            return token
        token_env = str(auth.get("token_env") or auth.get("tokenEnv") or "").strip()
        if token_env and _ENV_NAME_RE.fullmatch(token_env):
            return str(os.environ.get(token_env) or "").strip()
        return ""

    def _pin_url(self, url: str) -> Any:
        if self._url_pinner is not None:
            return self._url_pinner(url)
        return pin_outbound_http_url(url, allow_private=False)

    def _is_stdio_transport(self) -> bool:
        return (self.config.transport or "http").strip().casefold() == "stdio"

    def _capability_error(self) -> str:
        try:
            assert_capability_allowed(
                "mcp_server",
                self.config.name,
                payload=mcp_server_capability_payload(self.config),
            )
        except CapabilityManifestError as exc:
            return exc.message
        return ""
