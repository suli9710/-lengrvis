from __future__ import annotations

import pytest

from conftest import import_first, require_attr


PROVIDER_MODULES = (
    "backend.providers.fallback",
    "backend.llm.providers",
    "backend.core.providers",
    "mavris.providers.fallback",
)


class RecordingProvider:
    def __init__(self, name: str, result: str | None = None, error: Exception | None = None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = 0

    def complete(self, prompt: str, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return {"provider": self.name, "text": self.result or f"{self.name}: {prompt}"}


@pytest.fixture
def provider_api():
    module = import_first(PROVIDER_MODULES)
    return require_attr(
        module,
        ("ProviderFallback", "FallbackProvider", "fallback_complete", "complete_with_fallback"),
    )


def _complete(provider_api, providers, prompt: str):
    if isinstance(provider_api, type):
        instance = provider_api(providers)
        method = getattr(instance, "complete", None) or getattr(instance, "generate")
        return method(prompt)
    return provider_api(prompt=prompt, providers=providers)


def _provider_name(result):
    if isinstance(result, dict):
        return result.get("provider") or result.get("provider_name")
    return getattr(result, "provider", getattr(result, "provider_name", None))


def test_falls_back_to_next_provider_after_transient_error(provider_api):
    primary = RecordingProvider("primary", error=TimeoutError("temporary outage"))
    secondary = RecordingProvider("secondary", result="ok")

    result = _complete(provider_api, [primary, secondary], "hello")

    assert primary.calls == 1
    assert secondary.calls == 1
    assert _provider_name(result) == "secondary"


def test_does_not_call_fallback_when_primary_succeeds(provider_api):
    primary = RecordingProvider("primary", result="ok")
    secondary = RecordingProvider("secondary", result="unused")

    result = _complete(provider_api, [primary, secondary], "hello")

    assert primary.calls == 1
    assert secondary.calls == 0
    assert _provider_name(result) == "primary"
