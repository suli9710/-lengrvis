"""Entry point used by the official MCP client conformance runner.

The official runner intentionally hosts its scenario server on loopback.  This
script injects a loopback-capable URL pinner only inside the conformance
process; product MCP clients retain the public-network SSRF policy.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.outbound_url import pin_outbound_http_url  # noqa: E402
from app.mcp.client import MCPClient, MCPServerConfig  # noqa: E402


async def _run(server_url: str) -> None:
    scenario = str(os.environ.get("MCP_CONFORMANCE_SCENARIO") or "").strip()
    if scenario not in {"initialize", "tools_call", "tools-call", "sse-retry"}:
        raise RuntimeError(f"unsupported MCP conformance scenario: {scenario or '<missing>'}")
    protocol_version = (
        "2025-03-26"
        if scenario == "sse-retry"
        else str(
            os.environ.get("MCP_CONFORMANCE_PROTOCOL_VERSION") or "2025-11-25"
        ).strip()
    )
    client = MCPClient(
        MCPServerConfig(
            name="official-conformance",
            url=server_url,
            protocol_version=protocol_version,
            strict_lifecycle=True,
            client_name="lengrvis-conformance-client",
            client_version="0.1.2",
        ),
        url_pinner=lambda value: pin_outbound_http_url(value, allow_private=True),
    )
    try:
        tools = await client.list_tools(force_refresh=True)
        status = client.status()
        if status["state"] not in {"ready", "configured"}:
            raise RuntimeError(str(status.get("error") or "MCP client did not initialize"))
        if not isinstance(tools, list):
            raise RuntimeError("MCP tools/list did not return a list")
        if scenario in {"tools_call", "tools-call"}:
            advertised = {str(tool.get("name") or "") for tool in tools}
            if "add_numbers" not in advertised:
                raise RuntimeError("MCP conformance server did not advertise add_numbers")
            result = await client.call_tool("add_numbers", {"a": 2, "b": 3})
            if result.get("ok") is not True:
                raise RuntimeError("MCP conformance tool call failed")
        elif scenario == "sse-retry":
            advertised = {str(tool.get("name") or "") for tool in tools}
            if "test_reconnection" not in advertised:
                raise RuntimeError("MCP conformance server did not advertise test_reconnection")
            result = await client.call_tool("test_reconnection", {})
            if result.get("ok") is not True:
                raise RuntimeError("MCP SSE retry conformance call failed")
    finally:
        await client.close()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: mcp_conformance_client.py <server-url>", file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(sys.argv[1]))
    except Exception as exc:  # noqa: BLE001 - CLI boundary emits only the error type.
        print(f"MCP conformance client failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
