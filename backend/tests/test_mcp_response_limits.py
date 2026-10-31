from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.outbound_url import PinnedOutboundRequest
from app.mcp.client import MCPClient, MCPServerConfig
from app.mcp.http_stream import post_streaming_http
from app.mcp.protocol import MAX_RESPONSE_BYTES


class _LargeStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.chunks_read = 0
        self.closed = False

    async def __aiter__(self):
        for _ in range(80):
            self.chunks_read += 1
            yield b"x" * (64 * 1024)

    async def aclose(self):
        self.closed = True


def _factory(stream: _LargeStream, *, status: int = 200, content_type: str = "application/json"):
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        return real_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(status, headers={"Content-Type": content_type}, stream=stream)
            ),
        )

    return factory


def test_mcp_discovery_stops_reading_at_response_limit(monkeypatch):
    stream = _LargeStream()
    monkeypatch.setattr("app.mcp.client.httpx.AsyncClient", _factory(stream))
    client = MCPClient(
        MCPServerConfig(name="bounded", url="https://api.example/mcp", strict_lifecycle=False),
        url_pinner=lambda _url: PinnedOutboundRequest(url="https://93.184.216.34/mcp"),
    )
    result = asyncio.run(client._post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    assert "size limit" in result["error"]["message"]
    assert stream.chunks_read == MAX_RESPONSE_BYTES // (64 * 1024) + 1
    assert stream.closed


@pytest.mark.parametrize(
    ("status", "content_type"),
    [(200, "application/json"), (500, "application/json"), (200, "text/event-stream")],
)
def test_mcp_streaming_bodies_stop_before_unbounded_buffering(status, content_type):
    stream = _LargeStream()
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
            http_client_factory=_factory(stream, status=status, content_type=content_type),
        )
    )
    assert "size limit" in result.message["error"]["message"]
    assert stream.chunks_read == MAX_RESPONSE_BYTES // (64 * 1024) + 1
    assert stream.closed


def test_mcp_session_delete_does_not_consume_response_body(monkeypatch):
    stream = _LargeStream()
    monkeypatch.setattr("app.mcp.client.httpx.AsyncClient", _factory(stream))
    client = MCPClient(
        MCPServerConfig(name="bounded", url="https://api.example/mcp"),
        url_pinner=lambda _url: PinnedOutboundRequest(url="https://93.184.216.34/mcp"),
    )
    client._session_id = "session-test"
    asyncio.run(client._delete_session())
    assert stream.chunks_read == 0
    assert stream.closed


def test_mcp_bounded_reader_preserves_compressed_json():
    import gzip

    from app.mcp.http_stream import read_bounded_response

    encoded = gzip.compress(b'{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')

    class CompressedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for offset in range(0, len(encoded), 7):
                yield encoded[offset : offset + 7]

    async def read():
        response = httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            stream=CompressedStream(),
        )
        return await read_bounded_response(response)

    response = asyncio.run(read())
    assert response.json()["result"] == {"ok": True}


def test_mcp_stream_cancellation_closes_body():
    async def exercise():
        started = asyncio.Event()
        closed = asyncio.Event()

        class WaitingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                yield b"data: "
                await asyncio.Event().wait()

            async def aclose(self):
                closed.set()

        def factory(**kwargs):
            return httpx.AsyncClient(
                **kwargs,
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200, headers={"Content-Type": "text/event-stream"}, stream=WaitingStream()
                    )
                ),
            )

        task = asyncio.create_task(
            post_streaming_http(
                pinned=PinnedOutboundRequest(url="https://93.184.216.34/mcp"),
                payload={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
                headers={},
                timeout=1,
                session_id="session-test",
                protocol_version="2025-11-25",
                auth_token="",
                handle_notification=AsyncMock(),
                http_client_factory=factory,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert closed.is_set()

    asyncio.run(exercise())


@pytest.mark.parametrize("status", [200, 500])
def test_mcp_resume_rejection_does_not_read_unneeded_body(status):
    large = _LargeStream()
    requests = []

    def handler(request):
        requests.append(request.method)
        if request.method == "POST":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=b"retry: 0\nid: resume-me\n\n",
            )
        return httpx.Response(status, headers={"Content-Type": "application/json"}, stream=large)

    def factory(**kwargs):
        return httpx.AsyncClient(**kwargs, transport=httpx.MockTransport(handler))

    result = asyncio.run(
        post_streaming_http(
            pinned=PinnedOutboundRequest(url="https://93.184.216.34/mcp"),
            payload={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            headers={},
            timeout=1,
            session_id="session-test",
            protocol_version="2025-11-25",
            auth_token="",
            handle_notification=AsyncMock(),
            http_client_factory=factory,
        )
    )
    assert "error" in result.message
    assert requests == ["POST", "GET"]
    assert large.chunks_read == 0
    assert large.closed
