"""Tests for P0-3 three-mode LLM provider routing.

These tests use the live `AppSettings` dataclass + `get_provider_for_mode` selector;
no real network is made because local backend detection is monkeypatched where
auto-detection is under test.
"""

from __future__ import annotations

import pytest

from app.config import AppSettings
from app.llm.local_provider import LocalBackend, LocalBackendUnavailable
from app.llm.mock_provider import MockProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.registry import get_provider_for_mode
from app.policy.privacy import can_use_browser_network, can_use_browser_writes, can_use_cloud_model


def _cloud_settings(**overrides) -> AppSettings:
    base = AppSettings(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test-token",
        model="gpt-4o-mini",
        mode="efficiency",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _local_settings(**overrides) -> AppSettings:
    base = AppSettings(
        provider_name="ollama",
        base_url="http://127.0.0.1:11434/v1",
        api_key="",
        model="qwen2:1.5b",
        mode="privacy",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_privacy_mode_returns_local_or_mock_for_every_task():
    settings = _local_settings()
    for task in ("planner", "supervisor", "subagent", "embed", "vision", "ocr", "default"):
        provider = get_provider_for_mode(settings, task=task)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert "127.0.0.1" in provider.settings.base_url


def test_privacy_mode_without_local_url_detects_local_backend(monkeypatch):
    monkeypatch.setattr(
        "app.llm.registry.detect_local_backend",
        lambda: LocalBackend("lmstudio", "http://127.0.0.1:1234/v1", ["local-model"]),
    )
    settings = _local_settings(base_url="")
    provider = get_provider_for_mode(settings, task="planner")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.settings.provider_name == "lmstudio"
    assert provider.settings.base_url == "http://127.0.0.1:1234/v1"


def test_privacy_mode_without_local_backend_fails(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    settings = _local_settings(base_url="")
    with pytest.raises(LocalBackendUnavailable):
        get_provider_for_mode(settings, task="planner")


def test_privacy_mode_blocks_local_provider_with_remote_url():
    settings = _local_settings(base_url="https://example.com/v1")

    with pytest.raises(LocalBackendUnavailable):
        get_provider_for_mode(settings, task="planner")


def test_privacy_mode_blocks_url_that_only_contains_localhost_text():
    settings = _local_settings(base_url="https://example.com/localhost/v1")

    with pytest.raises(LocalBackendUnavailable):
        get_provider_for_mode(settings, task="planner")


def test_efficiency_mode_routes_every_task_to_cloud():
    settings = _cloud_settings()
    for task in ("planner", "supervisor", "subagent", "embed", "vision", "ocr"):
        provider = get_provider_for_mode(settings, task=task)
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.settings.api_key == "sk-test-token"


def test_default_settings_are_cloud_first(monkeypatch):
    def fail_local_probe():
        raise AssertionError("default settings must not probe local LLM backends")

    monkeypatch.setattr("app.llm.registry.detect_local_backend", fail_local_probe)
    monkeypatch.setattr("app.llm.registry.detect_onnx_backend", lambda settings: None)

    settings = AppSettings(api_key="sk-test-token")
    provider = get_provider_for_mode(settings, task="planner")

    assert settings.mode == "efficiency"
    assert settings.provider_name == "openai_compatible"
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.settings.api_key == "sk-test-token"


def test_efficiency_without_api_key_drops_to_mock():
    settings = _cloud_settings(api_key="")
    provider = get_provider_for_mode(settings, task="planner")
    assert isinstance(provider, MockProvider)


def test_hybrid_routes_planner_supervisor_to_cloud_others_local(monkeypatch):
    monkeypatch.setattr(
        "app.llm.registry.detect_local_backend",
        lambda: LocalBackend("llamacpp", "http://127.0.0.1:8080/v1", ["tinyllama"]),
    )
    settings = _cloud_settings(mode="hybrid", base_url="https://api.openai.com/v1")
    cloud = get_provider_for_mode(settings, task="planner")
    assert isinstance(cloud, OpenAICompatibleProvider)
    assert "127.0.0.1" not in cloud.settings.base_url

    # Subagent / embed should stay local and use the detected local backend.
    sub = get_provider_for_mode(settings, task="subagent")
    assert isinstance(sub, OpenAICompatibleProvider)
    assert sub.settings.provider_name == "llamacpp"
    assert "127.0.0.1" in sub.settings.base_url


def test_hybrid_vision_respects_allow_cloud_context(monkeypatch):
    cloud_allowed = _cloud_settings(mode="hybrid", allow_cloud_context=True)
    assert isinstance(get_provider_for_mode(cloud_allowed, task="vision"), OpenAICompatibleProvider)

    monkeypatch.setattr(
        "app.llm.registry.detect_local_backend",
        lambda: LocalBackend("ollama", "http://127.0.0.1:11434/v1", ["qwen2"]),
    )
    cloud_blocked = _cloud_settings(mode="hybrid", allow_cloud_context=False)
    provider = get_provider_for_mode(cloud_blocked, task="vision")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.settings.provider_name == "ollama"


@pytest.mark.parametrize("mode,task,expected", [
    ("privacy", "planner", False),
    ("efficiency", "subagent", True),
    ("hybrid", "planner", True),
    ("hybrid", "subagent", False),
    ("hybrid", "vision", False),  # allow_cloud_context default False
])
def test_can_use_cloud_model_matrix(mode: str, task: str, expected: bool):
    settings = AppSettings(provider_name="openai", api_key="x", mode=mode)
    assert can_use_cloud_model(settings, task=task).allowed is expected


def test_can_use_browser_writes_requires_efficiency_or_hybrid_with_cloud_context():
    privacy = AppSettings(provider_name="openai", api_key="x", mode="privacy", allow_browser_network=True)
    assert can_use_browser_writes(privacy).allowed is False

    efficiency = AppSettings(provider_name="openai", api_key="x", mode="efficiency", allow_browser_network=True)
    assert can_use_browser_writes(efficiency).allowed is True

    hybrid_no_cloud = AppSettings(provider_name="openai", api_key="x", mode="hybrid", allow_browser_network=True)
    assert can_use_browser_writes(hybrid_no_cloud).allowed is False

    hybrid_with_cloud = AppSettings(
        provider_name="openai",
        api_key="x",
        mode="hybrid",
        allow_browser_network=True,
        allow_cloud_context=True,
    )
    assert can_use_browser_writes(hybrid_with_cloud).allowed is True


def test_can_use_browser_network_blocks_in_privacy_even_when_flag_enabled():
    """P0-2: privacy mode must hard-block all browser network access regardless of allow_browser_network."""
    privacy = AppSettings(provider_name="openai", api_key="x", mode="privacy", allow_browser_network=True)
    assert can_use_browser_network(privacy).allowed is False

    efficiency = AppSettings(provider_name="openai", api_key="x", mode="efficiency", allow_browser_network=True)
    assert can_use_browser_network(efficiency).allowed is True

    efficiency_disabled = AppSettings(provider_name="openai", api_key="x", mode="efficiency", allow_browser_network=False)
    assert can_use_browser_network(efficiency_disabled).allowed is False


def test_policy_engine_settings_injection_is_optional():
    """PolicyEngine() with no args must still work for the legacy 22-test suite."""
    from app.policy.policy_engine import PolicyEngine

    engine = PolicyEngine()
    assert engine.settings is None

    with_settings = PolicyEngine(_cloud_settings())
    assert with_settings.settings is not None
    assert with_settings.settings.mode == "efficiency"


def test_settings_mode_coerces_invalid_value_to_privacy():
    from app.services.settings_service import _coerce_setting_value

    assert _coerce_setting_value("mode", "Privacy") == "privacy"
    assert _coerce_setting_value("mode", "EFFICIENCY") == "efficiency"
    assert _coerce_setting_value("mode", "broken") == "privacy"
    assert _coerce_setting_value("mode", 42) == "privacy"


def test_settings_mcp_servers_validates_structure():
    from app.services.settings_service import _coerce_setting_value

    valid = _coerce_setting_value(
        "mcp_servers",
        [
            {"name": "ok", "url": "https://mcp.example/", "enabled": True},
            {"name": "no-url"},
            {"url": "https://only-url/"},
            "not-a-dict",
        ],
    )
    assert isinstance(valid, list)
    assert len(valid) == 2
    assert valid[0]["name"] == "ok"
    assert valid[1]["name"] == "mcp"
    assert all(item["url"].startswith("http") for item in valid)
