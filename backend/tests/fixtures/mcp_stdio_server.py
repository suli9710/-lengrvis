"""Tiny deterministic MCP stdio server used by transport regression tests."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

TOOLS = [
    {
        "name": "inspect_env",
        "description": "Return the explicitly delegated test environment.",
        "inputSchema": {"type": "object", "additionalProperties": False},
    }
]


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()


for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        _send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "stdio-fixture", "version": "1"},
                },
            }
        )
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        _send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = message.get("params") or {}
        progress_token = ((params.get("_meta") or {}).get("progressToken"))
        if progress_token is not None:
            _send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {"progressToken": progress_token, "progress": 1, "total": 2},
                }
            )
            _send(
                {
                    "jsonrpc": "2.0",
                    "method": "notifications/progress",
                    "params": {
                        "progressToken": progress_token,
                        "progress": 2,
                        "total": 2,
                        "message": "complete",
                    },
                }
            )
        _send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "structuredContent": {
                        "fixed": os.environ.get("MCP_FIXED", ""),
                        "inherited": os.environ.get("MCP_INHERITED", ""),
                        "openai_key_present": bool(os.environ.get("OPENAI_API_KEY")),
                    }
                },
            }
        )
    else:
        _send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )
