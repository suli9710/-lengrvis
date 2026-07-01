from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from app.config import AppSettings
from app.context_management import PromptTooLongError
from app.core.outbound_url import PinnedOutboundRequest
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.openai_compatible import (
    _CIRCUITS,
    LLMApiCircuitOpen,
    OpenAICompatibleProvider,
    circuit_snapshot,
    close_shared_http_client,
    normalize_openai_base_url,
)
from app.llm.registry import get_provider_for_mode
from app.llm.structured_output import (
    LLMApiResponseError,
    LLMStructuredOutputError,
)
from app.llm.structured_output import (
    validate_structured_payload_lightweight as _validate_structured_payload_lightweight,
)


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

    async def post(self, url, headers=None, json=None, **kwargs):  # noqa: ANN001, A002, ARG002
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
    asyncio.run(close_shared_http_client())


def _patch_shared_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.llm.openai_compatible._shared_http_client", lambda: FakeAsyncClient())
    # Keep these tests hermetic: the connect-time SSRF pin would otherwise hit
    # the host resolver (and fake-IP tunnel setups rewrite every hostname).
    monkeypatch.setattr(
        "app.llm.openai_compatible.pin_outbound_http_url",
        lambda url, *, allow_private=False: PinnedOutboundRequest(url=url),
    )
    monkeypatch.setattr(
        "app.llm.openai_compatible.validate_outbound_http_url",
        lambda url, *, allow_private=False: url,
    )


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


def _text_response(status_code: int, text: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        text=text,
        headers=headers or {},
        request=httpx.Request("POST", "https://api.openai.com/v1/chat/completions"),
    )


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("https://api.example.test", "https://api.example.test/v1"),
        ("https://api.example.test/", "https://api.example.test/v1"),
        ("https://api.example.test/v1", "https://api.example.test/v1"),
        ("https://api.example.test/custom/openai", "https://api.example.test/custom/openai"),
    ],
)
def test_normalize_openai_base_url(raw, normalized):
    assert normalize_openai_base_url(raw) == normalized


def test_chat_uses_v1_for_bare_openai_compatible_base_url(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, {"choices": [{"message": {"content": "ok"}}]})]
    provider = OpenAICompatibleProvider(_settings(base_url="https://api.example.test"))

    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "ok"
    assert FakeAsyncClient.requests[0]["url"] == "https://api.example.test/v1/chat/completions"


def test_responses_uses_v1_for_bare_openai_compatible_base_url(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, {"status": "completed", "output": [{"content": [{"text": "ok"}]}]})]
    provider = OpenAICompatibleProvider(_settings(base_url="https://api.example.test", wire_api="responses"))

    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "ok"
    assert FakeAsyncClient.requests[0]["url"] == "https://api.example.test/v1/responses"


def test_chat_posts_to_pinned_ip_with_host_and_sni_preserved(monkeypatch):
    """Connect-time SSRF pin: the request goes to the validated IP, while the
    Host header and TLS SNI keep the real hostname."""
    monkeypatch.setattr("app.llm.openai_compatible._shared_http_client", lambda: FakeAsyncClient())
    monkeypatch.setattr(
        "app.llm.openai_compatible.validate_outbound_http_url",
        lambda url, *, allow_private=False: url,
    )
    monkeypatch.setattr(
        "app.llm.openai_compatible.pin_outbound_http_url",
        lambda url, *, allow_private=False: PinnedOutboundRequest(
            url=url.replace("api.example.test", "93.184.216.34"),
            headers={"Host": "api.example.test"},
            extensions={"sni_hostname": "api.example.test"},
        ),
    )
    FakeAsyncClient.responses = [_response(200, {"choices": [{"message": {"content": "ok"}}]})]
    provider = OpenAICompatibleProvider(_settings(base_url="https://api.example.test"))

    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "ok"
    request = FakeAsyncClient.requests[0]
    assert request["url"] == "https://93.184.216.34/v1/chat/completions"
    assert request["headers"]["Host"] == "api.example.test"


def test_circuit_snapshot_uses_normalized_base_url(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(503, {"error": "down"})]
    settings = _settings(
        base_url="https://api.example.test/",
        llm_api_max_retries=0,
        llm_api_circuit_failure_threshold=1,
    )
    provider = OpenAICompatibleProvider(settings)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert circuit_snapshot(settings)["state"] == "open"
    assert ("openai", "https://api.example.test/v1", "chat", "gpt-4o-mini") in _CIRCUITS


def test_chat_retries_transient_http_error(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(500, {"error": "temporary"}),
        _response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    provider = OpenAICompatibleProvider(_settings())

    text = asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert text == "ok"
    assert FakeAsyncClient.calls == 2


def test_chat_result_metadata_includes_retry_trace(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(500, {"error": "temporary"}),
        _response(200, {"choices": [{"message": {"content": "ok"}}]}),
    ]
    provider = OpenAICompatibleProvider(_settings())

    result = asyncio.run(provider.chat_result([{"role": "user", "content": "hello"}]))

    trace = result.metadata["api_trace"]
    assert trace["ok"] is True
    assert trace["attempts"] == 2
    assert trace["retry_events"][0]["status_code"] == 500


def test_chat_rejects_non_json_success_payload(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_text_response(200, "<html>not json</html>", {"content-type": "text/html"})]
    provider = OpenAICompatibleProvider(_settings())

    with pytest.raises(LLMApiResponseError, match="non-JSON"):
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))


def test_circuit_opens_after_repeated_transient_failures(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(500, {"error": "first"}),
        _response(503, {"error": "second"}),
    ]
    provider = OpenAICompatibleProvider(_settings(llm_api_max_retries=0, llm_api_circuit_failure_threshold=2))
    message = [{"role": "user", "content": "hello"}]

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.chat(message))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(provider.chat(message))
    with pytest.raises(LLMApiCircuitOpen):
        asyncio.run(provider.chat(message))

    assert FakeAsyncClient.calls == 2


def test_prompt_too_long_does_not_retry_or_open_circuit(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(400, {"error": {"message": "context_length_exceeded: prompt too long"}}),
        _response(500, {"error": "would be consumed by a bad retry"}),
    ]
    provider = OpenAICompatibleProvider(_settings(llm_api_max_retries=2, llm_api_circuit_failure_threshold=1))

    with pytest.raises(PromptTooLongError) as exc_info:
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert FakeAsyncClient.calls == 1
    assert exc_info.value.provider == "openai"
    assert provider.transport_metadata()["prompt_too_long"]["provider"] == "openai"
    assert _CIRCUITS == {}


def test_prompt_too_long_parses_reported_token_gap(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(413, {"error": {"message": "prompt is too long: 137500 tokens > 135000 maximum"}}),
    ]
    provider = OpenAICompatibleProvider(_settings(llm_api_max_retries=2, llm_api_circuit_failure_threshold=1))

    with pytest.raises(PromptTooLongError) as exc_info:
        asyncio.run(provider.chat([{"role": "user", "content": "hello"}]))

    assert exc_info.value.actual_tokens == 137500
    assert exc_info.value.limit_tokens == 135000
    assert exc_info.value.token_gap == 2500
    assert FakeAsyncClient.calls == 1


def test_privacy_mode_does_not_silently_fallback_to_cloud_or_mock(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_onnx_backend", lambda settings: None)
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    settings = _settings(base_url="https://api.openai.com/v1", allow_mock_fallback=True)
    settings.mode = "privacy"

    with pytest.raises(LocalBackendUnavailable):
        get_provider_for_mode(settings, task="planner")


def test_retry_after_header_controls_retry_sleep(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    _patch_shared_client(monkeypatch)
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
    _patch_shared_client(monkeypatch)
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


def test_chat_payload_strips_non_provider_message_fields(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}),
    ]
    provider = OpenAICompatibleProvider(_settings())

    asyncio.run(
        provider.chat_result(
            [
                {
                    "id": "msg_1",
                    "role": "user",
                    "content": "hello",
                    "created_at": "now",
                    "metadata": {"secret": "nope"},
                }
            ]
        )
    )

    sent = FakeAsyncClient.requests[0]["json"]["messages"][0]
    assert sent == {"role": "user", "content": "hello"}


def test_structured_chat_validates_schema_and_extracts_embedded_json(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(
            200,
            {
                "choices": [
                    {
                        "message": {"content": 'Result:\n{"name":"Ada","items":[{"id":"a","count":2}]}\nDone.'},
                        "finish_reason": "stop",
                    }
                ]
            },
        ),
    ]
    schema = {
        "type": "object",
        "required": ["name", "items"],
        "properties": {
            "name": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "count"],
                    "properties": {
                        "id": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
    }
    provider = OpenAICompatibleProvider(_settings())

    payload = asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))

    assert payload == {"name": "Ada", "items": [{"id": "a", "count": 2}]}


def test_structured_chat_chat_completions_sends_native_json_schema(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(200, {"choices": [{"message": {"content": '{"name":"Ada"}'}, "finish_reason": "stop"}]}),
    ]
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    provider = OpenAICompatibleProvider(_settings(structured_output_mode="native", structured_output_repair_retries=0))

    payload = asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))

    assert payload == {"name": "Ada"}
    response_format = FakeAsyncClient.requests[0]["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert "type" not in response_format["json_schema"]
    assert response_format["json_schema"]["schema"] == schema
    assert response_format["json_schema"]["strict"] is True


def test_structured_chat_responses_sends_native_json_schema(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(200, {"status": "completed", "output": [{"content": [{"text": '{"name":"Ada"}'}]}]}),
    ]
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
        "additionalProperties": False,
    }
    provider = OpenAICompatibleProvider(
        _settings(wire_api="responses", structured_output_mode="native", structured_output_repair_retries=0)
    )

    payload = asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))

    assert payload == {"name": "Ada"}
    text_format = FakeAsyncClient.requests[0]["json"]["text"]["format"]
    assert text_format["type"] == "json_schema"
    assert text_format["schema"] == schema
    assert text_format["strict"] is True


def test_structured_chat_auto_falls_back_when_native_json_schema_is_unsupported(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(400, {"error": {"message": "unknown parameter: response_format.json_schema"}}),
        _response(200, {"choices": [{"message": {"content": '{"name":"Ada"}'}, "finish_reason": "stop"}]}),
    ]
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }
    provider = OpenAICompatibleProvider(_settings(structured_output_mode="auto", structured_output_repair_retries=0))

    payload = asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))

    assert payload == {"name": "Ada"}
    assert "response_format" in FakeAsyncClient.requests[0]["json"]
    assert "response_format" not in FakeAsyncClient.requests[1]["json"]
    assert "Return only valid JSON" in FakeAsyncClient.requests[1]["json"]["messages"][0]["content"]


def test_structured_chat_native_mode_fail_closes_when_json_schema_is_unsupported(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(400, {"error": {"message": "unknown parameter: response_format.json_schema"}}),
    ]
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }
    provider = OpenAICompatibleProvider(_settings(structured_output_mode="native", structured_output_repair_retries=0))

    with pytest.raises(LLMStructuredOutputError) as exc_info:
        asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))

    assert exc_info.value.failure_kind == "native_unsupported"


def test_structured_chat_prompt_repairs_invalid_json_once(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(
            200,
            {"choices": [{"message": {"content": "not json token=repair-secret-123"}, "finish_reason": "stop"}]},
        ),
        _response(200, {"choices": [{"message": {"content": '{"name":"Ada"}'}, "finish_reason": "stop"}]}),
    ]
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }
    provider = OpenAICompatibleProvider(_settings(structured_output_mode="prompt", structured_output_repair_retries=1))

    payload = asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))

    assert payload == {"name": "Ada"}
    assert FakeAsyncClient.calls == 2
    repair_prompt = FakeAsyncClient.requests[1]["json"]["messages"][0]["content"]
    assert "[REDACTED]" in repair_prompt
    assert "repair-secret-123" not in repair_prompt


def test_structured_chat_repair_failure_raises_sanitized_structured_error(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(
            200,
            {"choices": [{"message": {"content": "not json sk-abcdefghijklmnopqrst"}, "finish_reason": "stop"}]},
        ),
        _response(200, {"choices": [{"message": {"content": "still not json"}, "finish_reason": "stop"}]}),
    ]
    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {"name": {"type": "string"}},
    }
    provider = OpenAICompatibleProvider(_settings(structured_output_mode="prompt", structured_output_repair_retries=1))

    with pytest.raises(LLMStructuredOutputError) as exc_info:
        asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))

    assert exc_info.value.failure_kind == "not_json"
    assert "sk-abcdefghijklmnopqrst" not in str(exc_info.value)


def test_structured_chat_rejects_missing_required_field(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(200, {"choices": [{"message": {"content": '{"count": 1}'}, "finish_reason": "stop"}]}),
    ]
    schema = {
        "type": "object",
        "required": ["name", "count"],
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
        },
    }
    provider = OpenAICompatibleProvider(_settings(structured_output_repair_retries=0))

    with pytest.raises(LLMApiResponseError, match="structured response did not match output schema"):
        asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))


def test_structured_chat_rejects_wrong_nested_type(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [
        _response(
            200,
            {
                "choices": [
                    {
                        "message": {"content": '{"items":[{"id":"a","count":"two"}]}'},
                        "finish_reason": "stop",
                    }
                ]
            },
        ),
    ]
    schema = {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "count"],
                    "properties": {
                        "id": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                },
            }
        },
    }
    provider = OpenAICompatibleProvider(_settings(structured_output_repair_retries=0))

    with pytest.raises(LLMApiResponseError, match="structured response did not match output schema"):
        asyncio.run(provider.structured_chat([{"role": "user", "content": "return json"}], schema))


def test_lightweight_schema_validation_rejects_extra_fields_and_bad_array_item_type():
    schema = {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "additionalProperties": False,
    }

    with pytest.raises(LLMApiResponseError, match="unexpected field"):
        _validate_structured_payload_lightweight({"items": ["a"], "extra": True}, schema)
    with pytest.raises(LLMApiResponseError, match=r"\$\.items\[0\].*string"):
        _validate_structured_payload_lightweight({"items": [1]}, schema)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"error": {"message": "provider said no"}},
    ],
)
def test_chat_rejects_malformed_success_payload(monkeypatch, payload):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, payload)]
    provider = OpenAICompatibleProvider(_settings())

    with pytest.raises(LLMApiResponseError):
        asyncio.run(provider.chat_result([{"role": "user", "content": "hello"}]))


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "failed", "error": {"message": "failed"}},
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        {"status": "completed", "output": []},
        {"error": {"message": "provider said no"}},
    ],
)
def test_responses_api_rejects_failed_or_empty_success_payload(monkeypatch, payload):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, payload)]
    provider = OpenAICompatibleProvider(_settings(wire_api="responses"))

    with pytest.raises(LLMApiResponseError):
        asyncio.run(provider.chat_result([{"role": "user", "content": "hello"}]))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"error": {"message": "provider said no"}},
    ],
)
def test_vision_rejects_malformed_success_payload(monkeypatch, tmp_path, payload):
    image = tmp_path / "sample.png"
    image.write_bytes(b"not really an image but enough for base64")
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, payload)]
    provider = OpenAICompatibleProvider(_settings())

    with pytest.raises(LLMApiResponseError):
        asyncio.run(provider.vision(str(image), "describe"))


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable on this platform: {exc}")


def test_prepare_vision_image_path_rejects_resolved_non_image_suffix(monkeypatch, tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("TOPSECRET", encoding="utf-8")
    bait = tmp_path / "photo.png"

    def fake_resolve_authorized(path, allowed_directories):  # noqa: ANN001
        if Path(path) == bait:
            return secret
        from app.core.paths import resolve_authorized

        return resolve_authorized(path, allowed_directories)

    monkeypatch.setattr("app.core.paths.resolve_authorized", fake_resolve_authorized)
    provider = OpenAICompatibleProvider(_settings())

    _, error = provider._prepare_vision_image_path(str(bait), allowed_directories=[str(tmp_path)])

    assert error is not None
    assert "unsupported image format" in error


def test_vision_rejects_symlink_extension_bypass_without_reading_secret(monkeypatch, tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("TOPSECRET", encoding="utf-8")
    bait = tmp_path / "photo.png"
    _symlink_or_skip(bait, secret)
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, {"choices": [{"message": {"content": "unused"}}]})]
    provider = OpenAICompatibleProvider(_settings())

    result = asyncio.run(provider.vision(str(bait), "describe", allowed_directories=[str(tmp_path)]))

    assert "unsupported image format" in result
    assert FakeAsyncClient.calls == 0


def test_vision_rejects_symlink_without_authorized_directories(monkeypatch, tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("TOPSECRET", encoding="utf-8")
    bait = tmp_path / "photo.png"
    _symlink_or_skip(bait, secret)
    _patch_shared_client(monkeypatch)
    provider = OpenAICompatibleProvider(_settings())

    result = asyncio.run(provider.vision(str(bait), "describe"))

    assert "symbolic links require authorized directories" in result
    assert FakeAsyncClient.calls == 0


def test_vision_rejects_symlink_escape_with_allowed_directories(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    real_image = outside / "real.png"
    real_image.write_bytes(b"png-bytes")
    bait = workspace / "photo.png"
    _symlink_or_skip(bait, real_image)
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, {"choices": [{"message": {"content": "unused"}}]})]
    provider = OpenAICompatibleProvider(_settings())

    result = asyncio.run(provider.vision(str(bait), "describe", allowed_directories=[str(workspace)]))

    assert "invalid image path" in result
    assert FakeAsyncClient.calls == 0


def test_vision_reads_authorized_symlink_to_image_inside_sandbox(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_image = workspace / "real.png"
    real_image.write_bytes(b"png-bytes")
    bait = workspace / "link.png"
    _symlink_or_skip(bait, real_image)
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, {"choices": [{"message": {"content": "ok"}}]})]
    provider = OpenAICompatibleProvider(_settings())

    result = asyncio.run(provider.vision(str(bait), "describe", allowed_directories=[str(workspace)]))

    assert result == "ok"
    assert FakeAsyncClient.calls == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "failed", "error": {"message": "failed"}},
        {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        {"status": "completed", "output": []},
        {"error": {"message": "provider said no"}},
    ],
)
def test_responses_vision_rejects_failed_or_empty_success_payload(monkeypatch, tmp_path, payload):
    image = tmp_path / "sample.png"
    image.write_bytes(b"not really an image but enough for base64")
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, payload)]
    provider = OpenAICompatibleProvider(_settings(wire_api="responses"))

    with pytest.raises(LLMApiResponseError):
        asyncio.run(provider.vision(str(image), "describe"))


def test_responses_api_with_tool_role_messages_falls_back_to_chat_completions(monkeypatch):
    _patch_shared_client(monkeypatch)
    FakeAsyncClient.responses = [_response(200, {"choices": [{"message": {"content": "ok"}}]})]
    provider = OpenAICompatibleProvider(_settings(wire_api="responses"))

    result = asyncio.run(
        provider.chat_result(
            [
                {"role": "user", "content": "run tool"},
                {"role": "tool", "content": "tool output", "tool_call_id": "call_1"},
            ]
        )
    )

    assert result.content == "ok"
    assert result.metadata["wire_api"] == "chat_completions"
    assert result.metadata["wire_api_requested"] == "responses"
    assert result.metadata["wire_api_fallback_reason"] == "tool_message_mapping_requires_chat_completions"
    assert FakeAsyncClient.requests[0]["url"].endswith("/chat/completions")
    assert FakeAsyncClient.requests[0]["json"]["messages"][1]["role"] == "tool"


def test_auth_error_does_not_retry_or_open_circuit(monkeypatch):
    _patch_shared_client(monkeypatch)
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
    _patch_shared_client(monkeypatch)
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
    _patch_shared_client(monkeypatch)
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
    _patch_shared_client(monkeypatch)
    provider = OpenAICompatibleProvider(
        _settings(
            base_url="https://api.openai.com/v1",
            llm_api_max_retries=0,
            llm_api_circuit_failure_threshold=1,
            embedding_model="embed-a",
        )
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
