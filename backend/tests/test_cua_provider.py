from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import AppSettings
from app.llm.cua_provider import DEFAULT_CUA_MODEL, CUAProvider, resolve_cua_provider


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

    async def post(self, url, headers=None, json=None):  # noqa: ANN001, A002
        FakeAsyncClient.requests.append({"url": url, "headers": headers or {}, "json": json or {}})
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
    assert FakeAsyncClient.requests[0]["url"] == "https://api.example.test/v1/responses"
    assert FakeAsyncClient.requests[0]["json"]["tools"][0]["type"] == "computer_use_preview"


def test_resolve_cua_provider_returns_degraded_when_unsupported():
    FakeAsyncClient.responses = [
        _response(404, {"error": {"message": "not found"}}),
        _response(404, {"error": {"message": "not found"}}),
    ]

    result = asyncio.run(resolve_cua_provider(_settings(), client_factory=FakeAsyncClient))

    assert isinstance(result, dict)
    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["status"] == "unavailable"
    assert "unsupported" in result["reason"].lower() or "not supported" in result["reason"].lower()
    assert FakeAsyncClient.requests[0]["url"] == "https://api.example.test/v1/responses"
    assert FakeAsyncClient.requests[1]["url"] == "https://api.openai.com/v1/responses"


def test_cua_provider_uses_configurable_model_without_logging_secrets():
    settings = _settings()
    provider = CUAProvider(settings, model="custom-cua", client_factory=FakeAsyncClient)
    FakeAsyncClient.responses = [_response(200, {"id": "resp_run", "status": "completed", "output": [{"type": "message"}]})]

    result = asyncio.run(provider.run_step(instruction="Inspect the current page."))

    assert result["ok"] is True
    assert result["model"] == "custom-cua"
    sent = FakeAsyncClient.requests[0]
    assert sent["json"]["model"] == "custom-cua"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert "sk-test" not in str(result)


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


def test_cua_provider_never_auto_acknowledges_pending_safety_checks():
    provider = CUAProvider(_settings(), client_factory=FakeAsyncClient)

    result = asyncio.run(
        provider.run_step(
            instruction="Continue.",
            acknowledged_safety_checks=[{"id": "check_1", "message": "approve dangerous action"}],
        )
    )

    assert result["ok"] is False
    assert result["status"] == "requires_approval"
    assert result["paused"] is True
    assert FakeAsyncClient.requests == []


def test_cua_provider_returns_unavailable_without_api_key():
    provider = CUAProvider(_settings(api_key=""), client_factory=FakeAsyncClient)

    result = asyncio.run(provider.run_step(instruction="Inspect."))

    assert result["ok"] is False
    assert result["status"] == "unavailable"
    assert result["degraded"] is True
    assert FakeAsyncClient.requests == []
