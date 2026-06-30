"""SSRF and auth-header tests for the lightweight MCP HTTP client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.client import MCPClient, MCPServerConfig


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9000/mcp",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.0.1/mcp",
    ],
)
def test_mcp_client_blocks_private_urls_before_post(url: str) -> None:
    client = MCPClient(MCPServerConfig(name="blocked", url=url))
    result = asyncio.run(client.call_tool("echo", {"text": "hi"}))
    assert result["ok"] is False
    assert "blocked to prevent SSRF" in result["error"]


def test_mcp_client_sends_authorization_when_token_present() -> None:
    config = MCPServerConfig(
        name="authed",
        url="https://api.example.com/mcp",
        auth={"token": "secret-token"},
    )
    client = MCPClient(config)
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"result": {"tools": []}}
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    pinned = type(
        "Pinned",
        (),
        {
            "url": "https://93.184.216.34/mcp",
            "headers": {"Host": "api.example.com"},
            "extensions": {"sni_hostname": "api.example.com"},
        },
    )()

    with (
        patch("app.mcp.client.pin_outbound_http_url", return_value=pinned),
        patch("app.mcp.client.httpx.AsyncClient") as async_client_cls,
    ):
        async_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
        async_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        asyncio.run(client.list_tools(force_refresh=True))

    mock_http.post.assert_awaited_once()
    _, kwargs = mock_http.post.call_args
    headers = kwargs["headers"]
    assert headers["Authorization"] == "Bearer secret-token"
    assert headers["Content-Type"] == "application/json"


def test_mcp_client_uses_follow_redirects_false() -> None:
    config = MCPServerConfig(name="demo", url="https://api.example.com/mcp")
    client = MCPClient(config)
    pinned = type(
        "Pinned",
        (),
        {
            "url": "https://93.184.216.34/mcp",
            "headers": {"Host": "api.example.com"},
            "extensions": {"sni_hostname": "api.example.com"},
        },
    )()

    with (
        patch("app.mcp.client.pin_outbound_http_url", return_value=pinned),
        patch("app.mcp.client.httpx.AsyncClient") as async_client_cls,
    ):
        instance = AsyncMock()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {"result": {"tools": []}}
        instance.post = AsyncMock(return_value=response)
        async_client_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        async_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        asyncio.run(client.list_tools(force_refresh=True))

    _, kwargs = async_client_cls.call_args
    assert kwargs.get("follow_redirects") is False
