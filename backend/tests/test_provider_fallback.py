from __future__ import annotations

import pytest

from app.agents.planner_agent import PlannerAgent
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


def test_appsettings_default_disables_mock_fallback():
    assert AppSettings().allow_mock_fallback is False


def test_appsettings_from_sources_default_disables_mock_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing-config.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LENGRVIS_ALLOW_MOCK_FALLBACK", raising=False)

    assert AppSettings.from_sources().allow_mock_fallback is False


def test_appsettings_from_sources_allows_explicit_mock_fallback_opt_in(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing-config.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")

    assert AppSettings.from_sources().allow_mock_fallback is True


def test_efficiency_mode_without_api_key_fails_by_default():
    with pytest.raises(LocalBackendUnavailable, match="cloud provider without api_key"):
        get_provider_for_mode(
            _cloud_settings(api_key=""),
            task="planner",
        )


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


@pytest.mark.asyncio
async def test_planner_provider_failure_does_not_use_mock_by_default(monkeypatch, tmp_path):
    class BrokenProvider:
        async def structured_chat(self, messages, output_schema):  # noqa: ARG002
            raise RuntimeError("provider down")

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.agents.planner_agent.get_effective_settings",
        lambda: _cloud_settings(api_key="sk-test"),
    )
    monkeypatch.setattr(
        "app.agents.planner_agent.get_provider",
        lambda settings=None, task="default": BrokenProvider(),  # noqa: ARG005
    )

    with pytest.raises(RuntimeError, match="provider down"):
        await PlannerAgent().create_plan(
            "task-provider-failure",
            "Draft a market update",
            "efficiency",
            ["search.query"],
        )


@pytest.mark.asyncio
async def test_planner_invalid_plan_does_not_use_mock_by_default(monkeypatch, tmp_path):
    class InvalidPlanProvider:
        async def structured_chat(self, messages, output_schema):  # noqa: ARG002
            return {"goal": "bad", "steps": []}

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.agents.planner_agent.get_effective_settings",
        lambda: _cloud_settings(api_key="sk-test"),
    )
    monkeypatch.setattr(
        "app.agents.planner_agent.get_provider",
        lambda settings=None, task="default": InvalidPlanProvider(),  # noqa: ARG005
    )

    with pytest.raises(ValueError, match="Plan must contain at least one step"):
        await PlannerAgent().create_plan(
            "task-invalid-plan",
            "Draft a market update",
            "efficiency",
            ["search.query"],
        )


@pytest.mark.asyncio
async def test_planner_invalid_plan_uses_mock_when_explicitly_allowed(monkeypatch, tmp_path):
    class InvalidPlanProvider:
        async def structured_chat(self, messages, output_schema):  # noqa: ARG002
            return {"goal": "bad", "steps": []}

    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.agents.planner_agent.get_effective_settings",
        lambda: _cloud_settings(api_key="sk-test", allow_mock_fallback=True),
    )
    monkeypatch.setattr(
        "app.agents.planner_agent.get_provider",
        lambda settings=None, task="default": InvalidPlanProvider(),  # noqa: ARG005
    )

    plan = await PlannerAgent().create_plan(
        "task-invalid-plan-fallback",
        "Draft a market update",
        "efficiency",
        ["search.query"],
    )

    assert plan.steps
    assert plan.assumptions == ["Generated by MockProvider when no real provider is configured."]
