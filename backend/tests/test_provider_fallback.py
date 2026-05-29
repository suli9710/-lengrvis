from __future__ import annotations

import pytest

from app.config import AppSettings
from app.llm.local_provider import LocalBackend, LocalBackendUnavailable
from app.llm.mock_provider import MockProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.registry import get_provider_for_mode


def _cloud_settings(**overrides) -> AppSettings:
    settings = AppSettings(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        mode="efficiency",
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def test_provider_fallback_uses_real_app_contract():
    assert get_provider_for_mode.__module__ == "app.llm.registry"


def test_efficiency_mode_without_api_key_uses_mock_only_when_allowed():
    provider = get_provider_for_mode(
        _cloud_settings(api_key="", allow_mock_fallback=True),
        task="planner",
    )

    assert isinstance(provider, MockProvider)


def test_efficiency_mode_without_api_key_fails_when_mock_fallback_disabled():
    with pytest.raises(LocalBackendUnavailable, match="cloud provider without api_key"):
        get_provider_for_mode(
            _cloud_settings(api_key="", allow_mock_fallback=False),
            task="planner",
        )


def test_hybrid_local_task_falls_back_to_detected_local_backend(monkeypatch):
    monkeypatch.setattr(
        "app.llm.registry.detect_local_backend",
        lambda: LocalBackend("ollama", "http://127.0.0.1:11434/v1", ["qwen2.5:3b-instruct"]),
    )

    provider = get_provider_for_mode(
        _cloud_settings(api_key="sk-test", mode="hybrid", base_url="https://api.openai.com/v1"),
        task="subagent",
    )

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.settings.provider_name == "ollama"
    assert provider.settings.base_url == "http://127.0.0.1:11434/v1"
    assert provider.settings.model == "gpt-4o-mini"


def test_hybrid_local_task_with_no_backend_fails_instead_of_mocking(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)

    with pytest.raises(LocalBackendUnavailable, match="Privacy mode requires"):
        get_provider_for_mode(
            _cloud_settings(api_key="sk-test", mode="hybrid", base_url="https://api.openai.com/v1"),
            task="subagent",
        )
