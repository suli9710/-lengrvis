from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import AppSettings
from app.context_management import PromptTooLongError
from app.llm.openai_compatible import LLMApiCircuitOpen, OpenAICompatibleProvider, _CIRCUITS


class FakeAsyncClient:
    calls = 0
    requests: list[dict] = []
    responses: list[httpx.Response] = []
    errors: list[Exception] = []

    def __init__(self, *args, **kwargs):  # noqa: D107, ANN002, ANN003
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
        return None

    async def post(self, url, headers=None, json=None):  # noqa: ANN001, A002
        FakeAsyncClient.calls += 1
        FakeAsyncClient.requests.append({"url": url, "headers": headers, "json": json})
        if FakeAsyncClient.errors:
            raise FakeAsyncClient.errors.pop(0)
        return FakeAsyncClient.responses.pop(0)


@pytest.fixture(autouse=True)
def _clear_circuit_state():
    _CIRCUITS.clear()
    FakeAsyncClient.calls = 0
    FakeAsyncClient.requests = []
    FakeAsyncClient.responses = []
    FakeAsyncClient.errors = []
    yield
    _CIRCUITS.clear()


def _settings(**overrides) -> AppSettings:
    return AppSettings(
        provider_name="openai",
        api_key="sk-test",
        mode="efficiency",
        llm_api_max_retries=overrides.pop("llm_api_max_retries", 1),
        llm_api_retry_backoff_seconds=overrides.pop("llm_api_retry_backoff_seconds", 0),
        llm_api_circuit_failure_threshold=overrides.pop("llm_api_circuit_failure_threshold", 2),
        **overrides,
    )


def _response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


def _response_with_headers(status_code: int, payload: dict, headers: dict[str, str]) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        headers=headers,
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


def test_chat_retries_transient_http_error(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(500, {"error": "temporary"}),
        _response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    provider = OpenAICompatibleProvider(_settings())

    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "ok"
    assert FakeAsyncClient.calls == 2


def test_circuit_opens_after_repeated_transient_failures(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(500, {"error": "first"}),
        _response(503, {"error": "second"}),
    ]
    provider = OpenAICompatibleProvider(
        _settings(llm_api_max_retries=0, llm_api_circuit_failure_threshold=2)
    )
    message = [{"role": "user", "content": "hello"}]

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.chat(message))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.chat(message))
    with pytest.raises(LLMApiCircuitOpen):
        asyncio.run(provider.chat(message))

    assert FakeAsyncClient.calls == 2


def test_prompt_too_long_does_not_retry_or_open_circuit(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(400, {"error": {"message": "context_length_exceeded: prompt too long"}}),
        _response(500, {"error": "would be consumed by a bad retry"}),
    ]
    provider = OpenAICompatibleProvider(
        _settings(llm_api_max_retries=2, llm_api_circuit_failure_threshold=1)
    )

    with pytest.raises(PromptTooLongError):
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert FakeAsyncClient.calls == 1
    assert _CIRCUITS == {}


def test_retry_after_header_controls_retry_sleep(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    FakeAsyncClient.responses = [
        _response_with_headers(429, {"error": "slow down"}, {"Retry-After": "1.25"}),
        _response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    provider = OpenAICompatibleProvider(_settings(llm_api_retry_backoff_seconds=99))

    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "ok"
    assert sleeps == [1.25]


def test_chat_result_parses_usage(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(
            200,
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            },
        ),
    ]
    provider = OpenAICompatibleProvider(_settings())

    result = asyncio.run(provider.chat_result([{"role": "user", "content": "hello"}]))

    assert result.content == "ok"
    assert result.usage.prompt_tokens == 10
    assert result.usage.completion_tokens == 3
    assert result.usage.estimated is False
    assert result.finish_reason == "stop"


def test_responses_api_rejects_tool_role_messages():
    provider = OpenAICompatibleProvider(_settings(wire_api="responses"))

    with pytest.raises(NotImplementedError):
        asyncio.run(
            provider.chat(
                [
                    {"role": "user", "content": "run tool"},
                    {"role": "tool", "content": "tool output"},
                ]
            )
        )


def test_auth_error_does_not_retry_or_open_circuit(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.responses = [
        _response(401, {"error": "bad key"}),
        _response(200, {"choices": [{"message": {"content": "unused"}}]}),
    ]
    provider = OpenAICompatibleProvider(_settings(llm_api_max_retries=2, llm_api_circuit_failure_threshold=1))

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert FakeAsyncClient.calls == 1
    assert _CIRCUITS == {}


def test_timeout_and_429_retry(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    FakeAsyncClient.errors = [httpx.TimeoutException("slow")]
    FakeAsyncClient.responses = [
        _response(429, {"error": "rate limited"}),
        _response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    provider = OpenAICompatibleProvider(_settings(llm_api_max_retries=2))

    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "ok"
    assert FakeAsyncClient.calls == 3


def test_circuit_cooldown_allows_success_and_clears_state(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    provider = OpenAICompatibleProvider(
        _settings(llm_api_max_retries=0, llm_api_circuit_failure_threshold=1, llm_api_circuit_cooldown_seconds=0)
    )
    FakeAsyncClient.responses = [
        _response(503, {"error": "down"}),
        _response(200, {"choices": [{"message": {"content": "back"}}]}),
    ]

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))
    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "back"
    assert _CIRCUITS == {}


def test_circuit_isolated_by_endpoint_and_actual_model(monkeypatch):
    monkeypatch.setattr("app.llm.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    provider = OpenAICompatibleProvider(
        _settings(llm_api_max_retries=0, llm_api_circuit_failure_threshold=1, embedding_model="embed-a")
    )
    FakeAsyncClient.responses = [
        _response(503, {"error": "embedding down"}),
        _response(200, {"choices": [{"message": {"content": "chat still works"}}]}),
    ]

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.embed(["hello"]))
    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "chat still works"
    assert ("openai", "https://api.openai.com/v1", "embeddings", "embed-a") in _CIRCUITS
    assert ("openai", "https://api.openai.com/v1", "chat", "gpt-4o-mini") not in _CIRCUITS
