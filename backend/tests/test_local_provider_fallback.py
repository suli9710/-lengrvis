"""Tests for P0-2: privacy mode must prefer a real local LLM and reject silent Mock fallback."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.llm import local_provider
from app.llm.local_provider import LocalBackend, LocalBackendUnavailable, detect_local_backend
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.registry import get_provider_for_mode
from app.policy.privacy import can_use_browser_network


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, plan: dict[str, _FakeResponse]):
        self._plan = plan

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str):
        if url not in self._plan:
            raise RuntimeError(f"unexpected probe {url}")
        return self._plan[url]


def _factory_for(plan: dict[str, _FakeResponse]):
    return lambda: _FakeClient(plan)


def test_detect_local_backend_finds_ollama_first():
    plan = {
        "http://127.0.0.1:11434/api/tags": _FakeResponse(
            200,
            {"models": [{"name": "qwen2.5:3b-instruct"}, {"name": "phi3:mini"}]},
        ),
    }
    backend = detect_local_backend(client_factory=_factory_for(plan))
    assert backend is not None
    assert backend.kind == "ollama"
    assert "qwen2.5:3b-instruct" in backend.models


def test_detect_local_backend_falls_through_to_lmstudio():
    plan = {
        "http://127.0.0.1:11434/api/tags": _FakeResponse(500),
        "http://127.0.0.1:1234/v1/models": _FakeResponse(
            200, {"data": [{"id": "qwen2.5-3b-instruct"}]}
        ),
    }
    backend = detect_local_backend(client_factory=_factory_for(plan))
    assert backend is not None
    assert backend.kind == "lmstudio"
    assert "qwen2.5-3b-instruct" in backend.models


def test_detect_local_backend_falls_through_to_llamacpp():
    plan = {
        "http://127.0.0.1:11434/api/tags": _FakeResponse(500),
        "http://127.0.0.1:1234/v1/models": _FakeResponse(500),
        "http://127.0.0.1:8080/v1/models": _FakeResponse(
            200, {"data": [{"id": "tinyllama-1.1b-chat"}]}
        ),
    }
    backend = detect_local_backend(client_factory=_factory_for(plan))
    assert backend is not None
    assert backend.kind == "llamacpp"
    assert backend.base_url == "http://127.0.0.1:8080/v1"
    assert backend.models == ["tinyllama-1.1b-chat"]


def test_detect_local_backend_returns_none_when_no_endpoint():
    plan = {
        "http://127.0.0.1:11434/api/tags": _FakeResponse(500),
        "http://127.0.0.1:1234/v1/models": _FakeResponse(500),
        "http://127.0.0.1:8080/v1/models": _FakeResponse(500),
    }
    assert detect_local_backend(client_factory=_factory_for(plan)) is None


def test_privacy_mode_uses_detected_local_backend(monkeypatch):
    detected = LocalBackend(
        kind="ollama",
        base_url="http://127.0.0.1:11434/v1",
        models=["qwen2.5:3b-instruct"],
    )
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: detected)
    settings = AppSettings(provider_name="mock", base_url="", mode="privacy", model="")
    provider = get_provider_for_mode(settings, task="planner")
    assert isinstance(provider, OpenAICompatibleProvider)
    assert "127.0.0.1" in provider.settings.base_url
    assert provider.settings.model == "qwen2.5:3b-instruct"


def test_privacy_mode_with_no_backend_raises_even_if_mock_allowed(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    settings = AppSettings(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        api_key="not-applicable",
        mode="privacy",
        allow_mock_fallback=True,
    )
    with pytest.raises(LocalBackendUnavailable) as exc_info:
        get_provider_for_mode(settings, task="planner")
    message = str(exc_info.value)
    assert "Privacy mode requires a reachable local LLM backend" in message
    assert "Ollama" in message
    assert "LM Studio" in message
    assert "llama.cpp" in message


def test_hybrid_local_task_with_no_backend_raises(monkeypatch):
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    settings = AppSettings(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        mode="hybrid",
    )
    with pytest.raises(LocalBackendUnavailable):
        get_provider_for_mode(settings, task="subagent")


def test_privacy_policy_blocks_browser_network_even_with_local_backend_enabled():
    settings = AppSettings(
        provider_name="ollama",
        base_url="http://127.0.0.1:11434/v1",
        mode="privacy",
        allow_browser_network=True,
    )
    decision = can_use_browser_network(settings)
    assert decision.allowed is False
    assert "privacy" in decision.reason.lower()


def test_health_snapshot_unavailable(monkeypatch):
    monkeypatch.setattr("app.llm.local_provider.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.local_provider.onnx_health_snapshot", lambda settings=None: {"available": False})
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", lambda **kwargs: None)
    snapshot = local_provider.health_snapshot()
    assert snapshot["available"] is False
    assert snapshot["selected_backend"] is None
    assert snapshot["probe_order"] == ["onnx", "ollama", "lmstudio", "llamacpp"]
    assert "Privacy mode requires" in snapshot["error"]


def test_health_snapshot_available(monkeypatch):
    monkeypatch.setattr("app.llm.local_provider.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.local_provider.onnx_health_snapshot", lambda settings=None: {"available": False})
    detected = LocalBackend(kind="ollama", base_url="http://127.0.0.1:11434/v1", models=["x"])
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", lambda **kwargs: detected)
    snapshot = local_provider.health_snapshot()
    assert snapshot["available"] is True
    assert snapshot["selected_backend"]["kind"] == "ollama"
    assert snapshot["selected_backend"]["model"] == "x"
    assert snapshot["probe_order"][0] == "onnx"
    assert snapshot["kind"] == "ollama"
    assert snapshot["models"] == ["x"]


def test_settings_local_llm_health_route_reports_selected_backend(monkeypatch):
    monkeypatch.setattr("app.llm.local_provider.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.local_provider.onnx_health_snapshot", lambda settings=None: {"available": False})
    detected = LocalBackend(kind="lmstudio", base_url="http://127.0.0.1:1234/v1", models=["qwen"])
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", lambda **kwargs: detected)

    from app.main import create_app

    response = TestClient(create_app()).get("/api/settings/local-llm/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["selected_backend"]["kind"] == "lmstudio"
    assert payload["selected_backend"]["model"] == "qwen"


def test_root_health_omits_local_llm_snapshot_by_default(monkeypatch):
    def fail_local_probe(**kwargs):
        raise AssertionError("default health check should not probe local LLM backends")

    monkeypatch.setattr("app.llm.local_provider.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.local_provider.onnx_health_snapshot", lambda settings=None: {"available": False})
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", fail_local_probe)

    from app.main import create_app

    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "efficiency"
    assert "local_llm" not in payload


def test_root_health_includes_local_llm_snapshot_for_privacy_mode(monkeypatch):
    monkeypatch.setenv("MARVIS_MODE", "privacy")
    monkeypatch.setattr("app.llm.local_provider.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.local_provider.onnx_health_snapshot", lambda settings=None: {"available": False})
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", lambda **kwargs: None)

    from app.main import create_app

    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["local_llm"]["available"] is False
    assert payload["local_llm"]["selected_backend"] is None
