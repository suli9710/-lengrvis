"""SSRF and auth-header tests for the lightweight MCP HTTP client."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
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
        auth={"token": "secret-token", "resource": "https://api.example.com/mcp"},
        strict_lifecycle=False,
    )
    client = MCPClient(config)
    mock_http = AsyncMock()

    async def mock_post(_url, *, json, **_kwargs):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": json["id"], "result": {"tools": []}},
        )

    mock_http.post = AsyncMock(side_effect=mock_post)
    mock_http.stream = _mock_stream(mock_http.post)
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


@pytest.mark.parametrize(
    "auth",
    [
        {"token": "secret-token"},
        {"token": "secret-token", "resource": "https://other.example/mcp"},
    ],
)
def test_mcp_client_refuses_unbound_or_wrong_audience_bearer_tokens(auth) -> None:
    client = MCPClient(
        MCPServerConfig(
            name="authed",
            url="https://api.example.com/mcp",
            auth=auth,
            strict_lifecycle=False,
        )
    )

    with patch("app.mcp.client.httpx.AsyncClient") as async_client_cls:
        assert asyncio.run(client.list_tools(force_refresh=True)) == []

    async_client_cls.assert_not_called()
    assert "resource" in client.status()["error"].casefold()


def test_mcp_client_uses_follow_redirects_false() -> None:
    config = MCPServerConfig(name="demo", url="https://api.example.com/mcp", strict_lifecycle=False)
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

        async def mock_post(_url, *, json, **_kwargs):
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                json={"jsonrpc": "2.0", "id": json["id"], "result": {"tools": []}},
            )

        instance.post = AsyncMock(side_effect=mock_post)
        instance.stream = _mock_stream(instance.post)
        async_client_cls.return_value.__aenter__ = AsyncMock(return_value=instance)
        async_client_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        asyncio.run(client.list_tools(force_refresh=True))

    _, kwargs = async_client_cls.call_args
    assert kwargs.get("follow_redirects") is False
    assert kwargs.get("trust_env") is False


def test_mcp_session_delete_ignores_environment_proxy() -> None:
    from app.core.outbound_url import PinnedOutboundRequest

    client = MCPClient(MCPServerConfig(name="demo", url="https://api.example.com/mcp"))
    client._session_id = "session-test"
    with (
        patch(
            "app.mcp.client.pin_outbound_http_url", return_value=PinnedOutboundRequest(url="https://93.184.216.34/mcp")
        ),
        patch("app.mcp.client.httpx.AsyncClient") as client_factory,
    ):
        instance = AsyncMock()
        instance.stream = _mock_stream(AsyncMock(return_value=httpx.Response(204)))
        client_factory.return_value.__aenter__ = AsyncMock(return_value=instance)
        client_factory.return_value.__aexit__ = AsyncMock(return_value=None)
        asyncio.run(client._delete_session())
    assert client_factory.call_args.kwargs["trust_env"] is False


def test_mcp_streaming_http_ignores_environment_proxy(monkeypatch) -> None:
    from app.core.outbound_url import PinnedOutboundRequest
    from app.mcp.http_stream import post_streaming_http

    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return httpx.AsyncClient(
            **kwargs,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
            ),
        )

    result = asyncio.run(
        post_streaming_http(
            pinned=PinnedOutboundRequest(url="https://93.184.216.34/mcp"),
            payload={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            headers={},
            timeout=1,
            session_id=None,
            protocol_version="2025-11-25",
            auth_token="",
            handle_notification=AsyncMock(),
            http_client_factory=factory,
        )
    )
    assert options[0]["trust_env"] is False
    assert result.message["result"] == {"ok": True}


def _mock_stream(request):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def stream(_method, url, **kwargs):
        yield await request(url, **kwargs)

    return MagicMock(side_effect=stream)
