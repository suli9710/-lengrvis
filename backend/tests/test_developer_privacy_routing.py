from __future__ import annotations

import pytest

from app.config import AppSettings
from app.integrations import lengrvis_code
from app.orchestration.developer_engine import DeveloperExecutionEngine
from app.orchestration.execution_models import RunPhase


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "https://llm.example.test/v1",
        "https://8.8.8.8/v1",
        "",
        "http://169.254.169.254/v1",
        "ftp://127.0.0.1/v1",
    ],
)
def test_privacy_cli_env_rejects_cloud_or_invalid_endpoints(base_url):
    settings = AppSettings(mode="privacy", provider_name="local", base_url=base_url, api_key="cloud-test-key")

    with pytest.raises(ValueError, match="Privacy mode requires an explicitly configured local"):
        lengrvis_code.build_lengrvis_code_env(settings, base_env={})


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:11434/v1", "http://localhost:1234/v1", "http://[::1]:8080/v1"])
def test_privacy_cli_env_uses_only_explicit_local_provider_config(base_url):
    settings = AppSettings(
        mode="privacy", provider_name="local", base_url=base_url, api_key="local-key", model="local-model"
    )

    env = lengrvis_code.build_lengrvis_code_env(
        settings,
        base_env={
            "OPENAI_BASE_URL": "https://api.openai.com/v1",
            "OPENAI_API_KEY": "parent-cloud-key",
            "OPENAI_MODEL": "parent-cloud-model",
            **dict.fromkeys(lengrvis_code.OPENAI_MODEL_ENV_KEYS, "parent-cloud-model"),
        },
        passthrough_env={
            "OPENAI_BASE_URL": "https://llm.example.test/v1",
            "OPENAI_API_KEY": "passthrough-cloud-key",
            "OPENAI_MODEL": "cloud-model",
            **dict.fromkeys(lengrvis_code.OPENAI_MODEL_ENV_KEYS, "passthrough-cloud-model"),
        },
    )

    assert env["OPENAI_BASE_URL"] == base_url
    assert env["OPENAI_API_KEY"] == "local-key"
    assert env["OPENAI_MODEL"] == "local-model"
    assert all(env[key] == "local-model" for key in lengrvis_code.OPENAI_MODEL_ENV_KEYS)


@pytest.mark.parametrize("mode", ["efficiency", "hybrid"])
def test_nonprivacy_cli_env_preserves_configured_cloud_provider(mode):
    settings = AppSettings(
        mode=mode, provider_name="openai", base_url="https://llm.example.test/v1", api_key="cloud-key"
    )

    env = lengrvis_code.build_lengrvis_code_env(settings, base_env={})

    assert env["OPENAI_BASE_URL"] == settings.base_url
    assert env["OPENAI_API_KEY"] == settings.api_key


@pytest.mark.asyncio
async def test_developer_privacy_task_cannot_launch_configured_cloud_cli(monkeypatch, tmp_path):
    settings = AppSettings(
        mode="efficiency",
        provider_name="openai",
        base_url="https://llm.example.test/v1",
        api_key="cloud-key",
        allowed_directories=[str(tmp_path)],
    )
    env_modes = []
    runtime_calls = []
    builder = lengrvis_code.build_lengrvis_code_env

    def record_env(settings, **kwargs):
        env_modes.append(settings.mode)
        return builder(settings, **kwargs)

    def unexpected_runtime_probe(*args, **kwargs):
        runtime_calls.append("probe_or_launch")
        raise AssertionError("privacy rejection must occur before CLI probing or subprocess launch")

    monkeypatch.setattr(lengrvis_code, "build_lengrvis_code_env", record_env)
    monkeypatch.setattr(lengrvis_code, "diagnose_lengrvis_code_runtime", unexpected_runtime_probe)
    monkeypatch.setattr(lengrvis_code, "build_lengrvis_code_command", unexpected_runtime_probe)
    monkeypatch.setattr(lengrvis_code.asyncio, "create_subprocess_exec", unexpected_runtime_probe)
    engine = DeveloperExecutionEngine(settings=settings)
    state = await engine.start_run("inspect the repository code", "privacy", "developer")

    result = await engine.run_turn(state)

    assert env_modes == ["privacy"]
    assert runtime_calls == []
    assert result.state.phase == RunPhase.FAILED
    assert result.finished is True
    assert settings.mode == "efficiency"
