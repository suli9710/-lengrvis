"""Streaming HTTP/SSE Adapter for resumable MCP tool calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.mcp.protocol import (
    MAX_RESPONSE_BYTES,
    _decode_json_response,
    _decode_sse_event,
)

MAX_SSE_RECONNECT_ATTEMPTS = 3
MAX_SSE_RETRY_MILLISECONDS = 30_000
DEFAULT_SSE_RETRY_MILLISECONDS = 1_000


@dataclass(slots=True)
class MCPHTTPStreamResult:
    message: dict[str, Any]
    response_headers: dict[str, str] = field(default_factory=dict)
    session_expired: bool = False


async def post_streaming_http(
    *,
    pinned: Any,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
    session_id: str | None,
    protocol_version: str,
    auth_token: str,
    handle_notification: Callable[[dict[str, Any]], Awaitable[None]],
    http_client_factory: Callable[..., Any],
) -> MCPHTTPStreamResult:
    """POST an MCP request and resume an interrupted SSE response safely."""

    method = str(payload.get("method") or "")
    expected_id = payload.get("id")
    response_headers: dict[str, str] = {}
    try:
        async with http_client_factory(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "POST",
                pinned.url,
                json=payload,
                headers=headers,
                extensions=dict(pinned.extensions),
            ) as response:
                response_headers = _normalized_headers(response)
                if response.status_code == 404 and session_id and method != "initialize":
                    return MCPHTTPStreamResult(
                        _error("MCP session expired", "session_expired", status=404),
                        response_headers,
                        session_expired=True,
                    )
                if response.status_code >= 400:
                    return await _http_error_result(response, response_headers)
                content_type = response.headers.get("content-type", "").casefold()
                if "text/event-stream" not in content_type:
                    await response.aread()
                    return MCPHTTPStreamResult(
                        _decode_json_response(response, expected_id=expected_id),
                        response_headers,
                    )
                result, last_event_id, retry_milliseconds = await consume_sse_stream(
                    response,
                    expected_id=expected_id,
                    handle_notification=handle_notification,
                )
            if result is not None:
                return MCPHTTPStreamResult(result, response_headers)
            if expected_id is None or not session_id:
                return MCPHTTPStreamResult(
                    _error("MCP SSE stream closed before the response completed", "transport"),
                    response_headers,
                )

            for _attempt in range(MAX_SSE_RECONNECT_ATTEMPTS):
                retry_delay = DEFAULT_SSE_RETRY_MILLISECONDS if retry_milliseconds is None else retry_milliseconds
                if retry_delay > MAX_SSE_RETRY_MILLISECONDS:
                    return MCPHTTPStreamResult(
                        _error("MCP SSE retry delay exceeded the safety limit", "protocol"),
                        response_headers,
                    )
                await asyncio.sleep(retry_delay / 1000)
                resume_headers = {
                    "Accept": "text/event-stream",
                    **pinned.headers,
                    "MCP-Protocol-Version": protocol_version,
                    "MCP-Session-Id": session_id,
                }
                if last_event_id:
                    resume_headers["Last-Event-ID"] = last_event_id
                if auth_token:
                    resume_headers["Authorization"] = f"Bearer {auth_token}"
                async with client.stream(
                    "GET",
                    pinned.url,
                    headers=resume_headers,
                    extensions=dict(pinned.extensions),
                ) as resumed:
                    response_headers = _normalized_headers(resumed)
                    if resumed.status_code >= 400:
                        await resumed.aread()
                        return MCPHTTPStreamResult(
                            _error(
                                f"MCP SSE resume HTTP error {resumed.status_code}",
                                "transport",
                                status=resumed.status_code,
                            ),
                            response_headers,
                        )
                    resumed_session = response_headers.get("mcp-session-id")
                    if resumed_session and resumed_session != session_id:
                        return MCPHTTPStreamResult(
                            _error("MCP SSE resume changed the session id", "protocol"),
                            response_headers,
                        )
                    resumed_type = resumed.headers.get("content-type", "").casefold()
                    if "text/event-stream" not in resumed_type:
                        await resumed.aread()
                        return MCPHTTPStreamResult(
                            _error("MCP SSE resume returned a non-SSE response", "protocol"),
                            response_headers,
                        )
                    result, event_id, retry_update = await consume_sse_stream(
                        resumed,
                        expected_id=expected_id,
                        handle_notification=handle_notification,
                    )
                if event_id is not None:
                    last_event_id = event_id
                if retry_update is not None:
                    retry_milliseconds = retry_update
                if result is not None:
                    return MCPHTTPStreamResult(result, response_headers)
            return MCPHTTPStreamResult(
                _error("MCP SSE response exceeded the reconnect limit", "transport"),
                response_headers,
            )
    except httpx.HTTPError as exc:
        return MCPHTTPStreamResult(
            _error(f"transport error: {exc}", "transport"),
            response_headers,
        )
    except (ValueError, UnicodeError) as exc:
        return MCPHTTPStreamResult(
            _error(f"invalid response: {exc}", "decode"),
            response_headers,
        )


async def consume_sse_stream(
    response: httpx.Response,
    *,
    expected_id: Any,
    handle_notification: Callable[[dict[str, Any]], Awaitable[None]],
) -> tuple[dict[str, Any] | None, str | None, int | None]:
    lines: list[str] = []
    byte_count = 0
    last_event_id: str | None = None
    retry_milliseconds: int | None = None

    async def consume_event() -> dict[str, Any] | None:
        nonlocal last_event_id, retry_milliseconds
        event = _decode_sse_event("\n".join(lines))
        lines.clear()
        if event.event_id is not None:
            last_event_id = event.event_id
        if event.retry_milliseconds is not None:
            retry_milliseconds = event.retry_milliseconds
        message = event.message
        if message is None:
            return None
        if isinstance(message.get("method"), str) and message.get("id") is None:
            await handle_notification(message)
        if expected_id is not None and message.get("id") == expected_id:
            return message
        return None

    async for line in response.aiter_lines():
        byte_count += len(line.encode("utf-8")) + 1
        if byte_count > MAX_RESPONSE_BYTES:
            raise ValueError("MCP response exceeded the size limit")
        if line == "":
            result = await consume_event()
            if result is not None:
                return result, last_event_id, retry_milliseconds
        else:
            lines.append(line)
    if lines:
        result = await consume_event()
        if result is not None:
            return result, last_event_id, retry_milliseconds
    return None, last_event_id, retry_milliseconds


async def _http_error_result(
    response: httpx.Response,
    response_headers: dict[str, str],
) -> MCPHTTPStreamResult:
    await response.aread()
    try:
        body = _decode_json_response(response)
    except ValueError:
        body = {}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return MCPHTTPStreamResult(body, response_headers)
    status = response.status_code
    return MCPHTTPStreamResult(
        _error(
            f"MCP transport HTTP error {status}" if status >= 500 else f"MCP HTTP error {status}",
            "transport" if status >= 500 else "http",
            status=status,
        ),
        response_headers,
    )


def _normalized_headers(response: httpx.Response) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in response.headers.items()}


def _error(message: str, error_type: str, *, status: int | None = None) -> dict[str, Any]:
    detail: dict[str, Any] = {"message": message, "type": error_type}
    if status is not None:
        detail["status"] = status
    return {"error": detail}
