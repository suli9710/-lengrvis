from __future__ import annotations

import asyncio
import base64
import socket
from typing import Any

import httpx
import pytest

from app.config import AppSettings
from app.llm import cua_provider
from app.llm.cua_provider import DEFAULT_CUA_MODEL, CUAProvider, probe_cua_provider, resolve_cua_provider


@pytest.fixture(autouse=True)
def _mock_outbound_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the test cloud hostnames to fixed public IPs so the outbound
    SSRF validator (which performs real DNS) does not fail-closed on the
    unroutable ``.test`` names, and so URL pinning has a stable IP to pin to."""

    def fake_getaddrinfo(host: str, *args: Any, **kwargs: Any):  # noqa: ANN002, ANN003
        mapping = {"api.example.test": "93.184.216.34", "api.openai.com": "93.184.216.35"}
        ip = mapping.get(host)
        if ip is None:
            raise socket.gaierror(f"unmocked host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr("app.core.outbound_url.socket.getaddrinfo", fake_getaddrinfo)


class FakeAsyncClient:
    requests: list[dict[str, Any]] = []
    responses: list[httpx.Response] = []
    errors: list[Exception] = []

    def __init__(self, *args, **kwargs):  # noqa: D107, ANN002, ANN003
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None

    async def post(self, url, headers=None, json=None, extensions=None):  # noqa: ANN001, A002
        FakeAsyncClient.requests.append(
            {"url": url, "headers": headers or {}, "json": json or {}, "extensions": extensions or {}}
        )
        if FakeAsyncClient.errors:
            raise FakeAsyncClient.errors.pop(0)
        return FakeAsyncClient.responses.pop(0)


def setup_function():
    FakeAsyncClient.requests = []
    FakeAsyncClient.responses = []
    FakeAsyncClient.errors = []


def _settings(**overrides) -> AppSettings:
    data = {
        "provider_name": "openai_compatible",
        "base_url": "https://api.example.test",
        "api_key": "sk-test",
        "mode": "efficiency",
    }
    data.update(overrides)
    return AppSettings(**data)


def _response(status_code: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "https://api.example.test/v1/responses"),
    )


def test_resolve_cua_provider_auto_probes_openai_compatible_first():
    FakeAsyncClient.responses = [_response(200, {"id": "resp_probe", "status": "completed", "output": []})]

    provider = asyncio.run(resolve_cua_provider(_settings(), client_factory=FakeAsyncClient))

    assert isinstance(provider, CUAProvider)
    assert provider.source == "openai_compatible"
    assert provider.model == DEFAULT_CUA_MODEL
    # URL is pinned to the resolved IP; the original host is preserved in Host/SNI.
    assert FakeAsyncClient.requests[0]["url"] == "https://93.184.216.34/v1/responses"
    assert FakeAsyncClient.requests[0]["headers"]["Host"] == "api.example.test"
    assert FakeAsyncClient.requests[0]["extensions"]["sni_hostname"] == "api.example.test"
    assert FakeAsyncClient.requests[0]["json"]["tools"][0]["type"] == "computer_use_preview"


def test_resolve_cua_provider_returns_degraded_when_unsupported():
    FakeAsyncClient.responses = [_response(404, {"error": {"message": "not found"}})]

    result = asyncio.run(resolve_cua_provider(_settings(), client_factory=FakeAsyncClient))

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["status"] == "unavailable"
    assert "unsupported" in result["reason"].lower() or "not supported" in result["reason"].lower()
    assert FakeAsyncClient.requests[0]["headers"]["Host"] == "api.example.test"
    assert len(FakeAsyncClient.requests) == 1


def test_resolve_cua_provider_only_falls_back_to_official_openai_when_configured():
    FakeAsyncClient.responses = [
        _response(404, {"error": {"message": "not found"}}),
        _response(404, {"error": {"message": "not found"}}),
    ]

    result = asyncio.run(
        resolve_cua_provider(_settings(base_url="https://api.openai.com/v1"), client_factory=FakeAsyncClient)
    )

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert FakeAsyncClient.requests[0]["headers"]["Host"] == "api.openai.com"
    assert FakeAsyncClient.requests[1]["headers"]["Host"] == "api.openai.com"


def test_resolve_cua_provider_treats_bare_openai_origin_as_official():
    FakeAsyncClient.responses = [
        _response(404, {"error": {"message": "not found"}}),
        _response(404, {"error": {"message": "not found"}}),
    ]

    result = asyncio.run(
        resolve_cua_provider(_settings(base_url="https://api.openai.com"), client_factory=FakeAsyncClient)
    )

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert [request["url"] for request in FakeAsyncClient.requests] == [
        "https://93.184.216.35/v1/responses",
        "https://93.184.216.35/v1/responses",
    ]


def test_resolve_cua_provider_does_not_rewrite_custom_openai_host_path_to_official_v1():
    FakeAsyncClient.responses = [_response(404, {"error": {"message": "not found"}})]

    result = asyncio.run(
        resolve_cua_provider(_settings(base_url="https://api.openai.com/custom/v1"), client_factory=FakeAsyncClient)
    )

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert len(FakeAsyncClient.requests) == 1
    assert FakeAsyncClient.requests[0]["headers"]["Host"] == "api.openai.com"
    assert FakeAsyncClient.requests[0]["url"] == "https://93.184.216.35/custom/v1/responses"


def test_cua_provider_uses_configurable_model_without_logging_secrets():
    settings = _settings()
    provider = CUAProvider(settings, model="custom-cua", client_factory=FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(200, {"id": "resp_run", "status": "completed", "output": [{"type": "message"}]})
    ]

    result = asyncio.run(provider.run_step(instruction="Inspect the current page."))

    assert result["ok"] is True
    assert result["model"] == "custom-cua"
    sent = FakeAsyncClient.requests[0]
    assert sent["json"]["model"] == "custom-cua"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert "sk-test" not in str(result)


def test_cua_provider_accepts_small_inline_screenshot_data_url():
    screenshot = "data:image/png;base64," + base64.b64encode(b"png").decode("ascii")
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)
    FakeAsyncClient.responses = [_response(200, {"id": "resp_run", "status": "completed", "output": []})]

    result = asyncio.run(provider.run_step(instruction="Inspect the page.", screenshot=screenshot))

    assert result["ok"] is True
    content = FakeAsyncClient.requests[0]["json"]["input"][0]["content"]
    assert content[1] == {"type": "input_image", "image_url": screenshot}


def test_cua_provider_normalizes_browser_environment():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)
    FakeAsyncClient.responses = [_response(200, {"id": "resp_run", "status": "completed", "output": []})]

    result = asyncio.run(provider.run_step(instruction="Inspect the page.", environment="BROWSER"))

    assert result["ok"] is True
    assert FakeAsyncClient.requests[0]["json"]["tools"] == [
        {"type": "computer_use_preview", "environment": "browser"}
    ]


def test_cua_provider_rejects_non_browser_environment_without_request():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect the page.", environment="windows"))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert "browser environment" in result["reason"]
    assert FakeAsyncClient.requests == []


def test_cua_provider_rejects_previous_response_id_without_request():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect the page.", previous_response_id="resp_other_task"))

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "previous_response_id" in result["reason"]
    assert FakeAsyncClient.requests == []


@pytest.mark.parametrize(
    "screenshot, reason_fragment",
    [
        ("file:///C:/Users/Suli/Desktop/screen.png", "inline data:image"),
        ("https://example.com/screen.png", "inline data:image"),
        ("data:image/png;base64,@@not-base64@@", "valid base64"),
        ("data:text/plain;base64,aGVsbG8=", "inline data:image"),
        ("data:image/svg+xml;base64,PHN2Zz4=", "PNG, JPEG, or WebP"),
    ],
)
def test_cua_provider_rejects_unsafe_screenshot_inputs_without_request(screenshot: str, reason_fragment: str):
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect the page.", screenshot=screenshot))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert reason_fragment in result["reason"]
    assert FakeAsyncClient.requests == []


def test_cua_provider_rejects_oversized_screenshot_without_request():
    screenshot = "data:image/png;base64," + base64.b64encode(b"x" * (8 * 1024 * 1024 + 1)).decode("ascii")
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect the page.", screenshot=screenshot))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert "8 MB" in result["reason"]
    assert FakeAsyncClient.requests == []


def test_cua_provider_rejects_oversized_base64_before_decode(monkeypatch: pytest.MonkeyPatch):
    def fail_decode(*args: Any, **kwargs: Any) -> bytes:  # noqa: ARG001
        raise AssertionError("oversized CUA screenshot should be rejected before base64 decode")

    monkeypatch.setattr(cua_provider.base64, "b64decode", fail_decode)
    screenshot = "data:image/png;base64," + ("A" * (cua_provider.MAX_CUA_SCREENSHOT_BASE64_CHARS + 4))
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect the page.", screenshot=screenshot))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert "8 MB" in result["reason"]
    assert FakeAsyncClient.requests == []


def test_cua_provider_pauses_on_pending_safety_checks():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(
            200,
            {
                "id": "resp_pending",
                "status": "in_progress",
                "output": [
                    {
                        "type": "computer_call",
                        "pending_safety_checks": [
                            {"id": "check_1", "code": "payment", "message": "confirm before payment token abc"}
                        ],
                    }
                ],
            },
        )
    ]

    result = asyncio.run(provider.run_step(instruction="Continue."))

    assert result["ok"] is False
    assert result["status"] == "requires_approval"
    assert result["paused"] is True
    assert result["pending_safety_checks"][0]["id"] == "check_1"
    assert "abc" not in str(result)


def test_cua_provider_rejects_supplied_safety_check_acknowledgements():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)

    result = asyncio.run(
        provider.run_step(
            instruction="Continue.",
            acknowledged_safety_checks=[{"id": "check_1", "message": "approve dangerous action"}],
        )
    )

    assert result["ok"] is False
    assert result["status"] == "denied"
    assert "cannot be acknowledged" in result["reason"]
    assert "pending_safety_checks" not in result
    assert FakeAsyncClient.requests == []


def test_cua_provider_returns_unavailable_without_api_key():
    provider = CUAProvider(_settings(api_key=""), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect."))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["degraded"] is True
    assert FakeAsyncClient.requests == []


def test_cua_provider_redacts_secrets_from_transport_errors():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)
    FakeAsyncClient.errors = [
        RuntimeError(
            "upstream rejected Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345 "
            "api_key=sk-abcdefghijklmnopqrstuvwx token=secret-token-value"
        )
    ]

    result = asyncio.run(provider.run_step(instruction="Inspect."))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    reason = result["reason"]
    assert "Bearer abcdefghijklmnopqrstuvwxyz012345" not in reason
    assert "sk-abcdefghijklmnopqrstuvwx" not in reason
    assert "secret-token-value" not in reason
    assert "Bearer [REDACTED]" in reason


def test_probe_cua_provider_redacts_provider_error_payload_secrets():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(
            200,
            {
                "error": {
                    "message": "bad credentials Bearer abcdefghijklmnopqrstuvwxyz012345 "
                    "api_key=sk-abcdefghijklmnopqrstuvwx token=secret-token-value"
                }
            },
        )
    ]

    result = asyncio.run(probe_cua_provider(provider))

    assert result["available"] is False
    reason = result["reason"]
    assert "Bearer abcdefghijklmnopqrstuvwxyz012345" not in reason
    assert "sk-abcdefghijklmnopqrstuvwx" not in reason
    assert "secret-token-value" not in reason
    assert "Bearer [REDACTED]" in reason


def test_cua_provider_redacts_terminal_status_details():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(
            200,
            {
                "id": "resp_failed",
                "status": "incomplete",
                "incomplete_details": {
                    "reason": "tool failed with Bearer abcdefghijklmnopqrstuvwxyz012345",
                    "api_key": "sk-abcdefghijklmnopqrstuvwx",
                    "token": "secret-token-value",
                },
            },
        )
    ]

    result = asyncio.run(provider.run_step(instruction="Inspect."))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    reason = result["reason"]
    assert "Bearer abcdefghijklmnopqrstuvwxyz012345" not in reason
    assert "sk-abcdefghijklmnopqrstuvwx" not in reason
    assert "secret-token-value" not in reason
    assert "Bearer [REDACTED]" in reason


def test_cua_provider_blocks_ssrf_to_metadata_host():
    provider = CUAProvider(_settings(base_url="http://169.254.169.254/v1"), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect."))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert FakeAsyncClient.requests == []


def test_cua_provider_blocks_ssrf_to_loopback_host():
    provider = CUAProvider(_settings(base_url="http://127.0.0.1:8000/v1"), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect."))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert FakeAsyncClient.requests == []


def test_probe_cua_provider_blocks_ssrf_to_metadata_host():
    provider = CUAProvider(_settings(base_url="http://169.254.169.254/v1"), client_factory=FakeAsyncClient)

    result = asyncio.run(probe_cua_provider(provider))

    assert result["available"] is False
    assert FakeAsyncClient.requests == []


def test_cua_provider_unavailable_when_dns_resolution_fails():
    provider = CUAProvider(_settings(base_url="https://unresolved.invalid/v1"), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect."))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert FakeAsyncClient.requests == []


def test_cua_provider_allows_local_base_for_local_provider():
    provider = CUAProvider(
        _settings(provider_name="ollama", base_url="http://127.0.0.1:11434/v1"),
        client_factory=FakeAsyncClient,
    )
    FakeAsyncClient.responses = [_response(200, {"id": "resp_local", "status": "completed", "output": []})]

    result = asyncio.run(provider.run_step(instruction="Inspect."))

    assert result["ok"] is True
    assert FakeAsyncClient.requests[0]["url"] == "http://127.0.0.1:11434/v1/responses"
