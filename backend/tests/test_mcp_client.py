"""Tests for P1-7 MCP client + registry.

We stand up an in-process http server that speaks JSON-RPC 2.0 for `tools/list`
and `tools/call`, then point the MCPClient at it.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import threading

import pytest

from app.config import AppSettings
from app.mcp import MCPClient, MCPServerConfig, MCPRegistry
from app.tools.schemas import ToolDefinition


_MOCK_TOOLS = [
    {
        "name": "echo",
        "description": "Echo the provided text back",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
    {
        "name": "add",
        "description": "Sum two integers",
        "inputSchema": {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}},
    },
]


def _make_handler():
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            method = payload.get("method")
            response: dict
            if method == "tools/list":
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"tools": _MOCK_TOOLS}}
            elif method == "tools/call":
                params = payload.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                if name == "echo":
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"echo": args.get("text", "")}}
                elif name == "add":
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "result": {"sum": int(args.get("a", 0)) + int(args.get("b", 0))}}
                else:
                    response = {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32601, "message": f"unknown tool {name}"}}
            else:
                response = {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32600, "message": "invalid method"}}
            body = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@pytest.fixture
def mcp_server():
    server = socketserver.TCPServer(("127.0.0.1", 0), _make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        yield url
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_mcp_client_lists_tools(mcp_server):
    config = MCPServerConfig(name="demo", url=mcp_server)
    client = MCPClient(config)
    tools = asyncio.run(client.list_tools())
    names = [tool["name"] for tool in tools]
    assert "echo" in names and "add" in names
    # description and input_schema preserved
    echo_tool = next(tool for tool in tools if tool["name"] == "echo")
    assert echo_tool["description"] == "Echo the provided text back"
    assert echo_tool["input_schema"]["type"] == "object"


def test_mcp_client_calls_tool(mcp_server):
    config = MCPServerConfig(name="demo", url=mcp_server)
    client = MCPClient(config)
    result = asyncio.run(client.call_tool("add", {"a": 2, "b": 3}))
    assert result["ok"] is True
    assert result["result"]["sum"] == 5


def test_mcp_client_handles_unknown_tool(mcp_server):
    config = MCPServerConfig(name="demo", url=mcp_server)
    client = MCPClient(config)
    result = asyncio.run(client.call_tool("missing", {}))
    assert result["ok"] is False
    assert "unknown" in result["error"].lower()


def test_mcp_registry_adapts_to_tool_definitions(mcp_server):
    settings = AppSettings(
        provider_name="mock",
        mcp_servers=[{"name": "demo", "url": mcp_server, "transport": "http", "enabled": True}],
    )
    registry = MCPRegistry()
    registry.load_from_settings(settings)
    definitions = asyncio.run(registry.adapt_to_tool_definitions())
    names = [d.name for d in definitions]
    assert "mcp.demo.echo" in names and "mcp.demo.add" in names
    for definition in definitions:
        assert isinstance(definition, ToolDefinition)
        assert definition.agent_owner == "SearchAgent"


def test_mcp_registry_skips_disabled_servers():
    registry = MCPRegistry()
    registry.load_from_settings(
        AppSettings(
            provider_name="mock",
            mcp_servers=[
                {"name": "off", "url": "http://nowhere/", "enabled": False},
                {"name": "empty", "url": "", "enabled": True},
            ],
        )
    )
    assert registry.clients == {}


def test_mcp_client_transport_error_returns_inline_error():
    client = MCPClient(MCPServerConfig(name="demo", url="http://127.0.0.1:1/"))
    result = asyncio.run(client.call_tool("echo", {"text": "hi"}))
    assert result["ok"] is False
    assert "transport" in result["error"].lower()
