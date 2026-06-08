"""Offline privacy-mode evals for first-run local-model readiness."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import AppSettings
from app.core import db
from app.core.schemas import Plan
from app.llm.local_provider import LocalBackendUnavailable
from app.llm.registry import get_provider_for_mode
from app.product_task_templates import task_starter_prompt
from app.services import run_service
from app.tools import document_tools, system_tools


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing-config.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    for key in (
        "LENGRVIS_ALLOWED_DIRECTORIES",
        "LENGRVIS_MODE",
        "LENGRVIS_PROVIDER_NAME",
        "LENGRVIS_BASE_URL",
        "LENGRVIS_API_KEY",
        "LENGRVIS_ALLOW_MOCK_FALLBACK",
    ):
        monkeypatch.delenv(key, raising=False)
    db.init_db()


def test_privacy_mode_offline_blocks_cloud_and_reports_local_repair(monkeypatch):
    """A clean privacy-mode machine must fail locally and point to setup, not cloud fallback."""
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    monkeypatch.setattr("app.llm.local_provider.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.local_provider.onnx_health_snapshot", lambda settings=None: {"available": False})
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", lambda **kwargs: None)
    provider_attempts = []

    def _unexpected_provider(*args, **kwargs):  # noqa: ARG001
        provider_attempts.append(True)
        raise AssertionError("privacy/offline mode must not construct a cloud or mock provider")

    monkeypatch.setattr("app.llm.registry.OpenAICompatibleProvider", _unexpected_provider)
    monkeypatch.setattr("app.llm.registry.MockProvider", _unexpected_provider)
    settings = AppSettings(
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-test",
        mode="privacy",
        allow_mock_fallback=True,
    )

    with pytest.raises(LocalBackendUnavailable) as exc_info:
        get_provider_for_mode(settings, task="planner")

    assert "Privacy mode requires a reachable local LLM backend" in str(exc_info.value)
    assert "Start Ollama, LM Studio, or a llama.cpp-compatible OpenAI server, then retry" in str(exc_info.value)
    assert provider_attempts == []

    monkeypatch.setattr("app.services.ollama_service.is_installed", lambda: False)

    def _ready_for_local_setup(model=None):
        return {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
            "memory_total_bytes": 16 * 1024**3,
            "disk_free_bytes": 64 * 1024**3,
            "cpu_logical_cores": 8,
            "gpu_summary": "",
        }

    monkeypatch.setattr("app.services.ollama_service.hardware_readiness", _ready_for_local_setup)
    monkeypatch.setattr("app.llm.local_provider.hardware_readiness", _ready_for_local_setup)
    client = TestClient(__import__("app.main", fromlist=["create_app"]).create_app())

    health = client.get("/api/settings/local-llm/health")
    assert health.status_code == 200
    health_payload = health.json()
    assert health_payload["available"] is False
    assert health_payload["selected_backend"] is None
    assert health_payload["probe_order"] == ["onnx", "ollama", "lmstudio", "llamacpp"]
    assert "Privacy mode requires" in health_payload["error"]
    assert health_payload["readiness"]["can_install"] is True
    assert health_payload["readiness"]["recommended_model"] == "qwen2.5:3b"

    plan = client.get("/api/settings/local-model/setup-plan?model=qwen2.5:3b")
    assert plan.status_code == 200
    plan_payload = plan.json()
    steps_by_key = {step["key"]: step for step in plan_payload["steps"]}
    assert plan_payload["ready"] is False
    assert plan_payload["can_install"] is True
    assert plan_payload["model"] == "qwen2.5:3b"
    assert plan_payload["installed"] is False
    assert plan_payload["running"] is False
    assert plan_payload["next_action"] == "install_runtime"
    assert steps_by_key["hardware"]["state"] == "done"
    assert steps_by_key["runtime"]["state"] == "current"
    assert "Ollama" in steps_by_key["runtime"]["detail"]
    assert steps_by_key["server"]["state"] == "pending"
    assert steps_by_key["model"]["state"] == "pending"
    assert "privacy mode" in steps_by_key["model"]["detail"]


def test_privacy_mode_offline_file_search_reports_scope_and_searches_locally(monkeypatch, tmp_path):
    """File search stays inside local boundaries when privacy mode has no local LLM."""
    monkeypatch.setenv("LENGRVIS_MODE", "privacy")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "openai")
    monkeypatch.setenv("LENGRVIS_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-test")
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)

    def _unexpected_provider(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("privacy/offline file search must not construct an LLM provider")

    monkeypatch.setattr("app.llm.registry.OpenAICompatibleProvider", _unexpected_provider)
    monkeypatch.setattr("app.llm.registry.MockProvider", _unexpected_provider)
    client = TestClient(__import__("app.main", fromlist=["create_app"]).create_app())

    missing_scope = client.get("/api/files/search?q=needle")
    assert missing_scope.status_code == 200
    assert missing_scope.json()["name_search"] == {
        "count": 0,
        "scanned": 0,
        "truncated": False,
        "status": "missing_scope",
    }

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "needle-local-report.txt").write_text("local only", encoding="utf-8")
    (workspace / "unrelated-note.txt").write_text("nothing to see", encoding="utf-8")
    db.set_setting("allowed_directories", [str(workspace)])

    local_search = client.get("/api/files/search?q=needle")
    assert local_search.status_code == 200
    payload = local_search.json()
    assert payload["name_search"]["count"] == 1
    assert payload["name_search"]["scanned"] == 2
    assert {item["name"] for item in payload["name_results"]} == {"needle-local-report.txt"}
    assert payload["index_results"] == []


def test_privacy_mode_offline_system_check_completes_with_local_diagnostics(monkeypatch):
    """The first-screen computer check must remain useful before a local LLM is installed."""
    monkeypatch.setenv("LENGRVIS_MODE", "privacy")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "openai")
    monkeypatch.setenv("LENGRVIS_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-system-check-secret")
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    monkeypatch.setattr("app.llm.local_provider.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.local_provider.onnx_health_snapshot", lambda settings=None: {"available": False})
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", lambda **kwargs: None)

    def _unexpected_provider(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("offline system check must not construct an LLM provider")

    def _ready_for_local_setup(model=None):
        return {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        }

    monkeypatch.setattr("app.agents.planner_agent.get_provider", _unexpected_provider)
    monkeypatch.setattr("app.agents.planner_agent.MockProvider", _unexpected_provider)
    monkeypatch.setattr("app.services.ollama_service.hardware_readiness", _ready_for_local_setup)
    monkeypatch.setattr("app.llm.local_provider.hardware_readiness", _ready_for_local_setup)
    client = TestClient(__import__("app.main", fromlist=["create_app"]).create_app())

    created = client.post(
        "/api/runs",
        json={"message": task_starter_prompt("check-computer"), "mode": "privacy", "engine": "os"},
    )
    assert created.status_code == 200
    run = created.json()
    assert run["engine"] == "os"

    final = _wait_for_run_phase(client, run["run_id"], "completed", "failed", "denied")
    assert final["phase"] == "completed"

    plan = Plan.model_validate(db.fetch_many("plans", "task_id = ?", (final["task_id"],), limit=1)[0])
    assert [(step.agent_name, step.tool_name, step.status.value) for step in plan.steps] == [
        ("ComputerAgent", "system.diagnostics", "succeeded")
    ]
    assert plan.requires_user_approval is False
    assert plan.global_risk_level.value == "R0_READ_ONLY"

    tool_result = db.fetch_many("tool_results", limit=1)[0]
    assert tool_result["ok"] is True
    output = tool_result["output"]
    assert output["local_ai"]["scope"] == "local_only"
    assert output["local_ai"]["available"] is False
    assert output["local_ai"]["readiness"]["can_install"] is True
    assert output["local_ai"]["readiness"]["recommended_model"] == "qwen2.5:3b"
    assert "Privacy mode requires" in output["local_ai"]["error"]

    encoded = json.dumps([final, plan.model_dump(mode="json"), output], ensure_ascii=False)
    assert "sk-system-check-secret" not in encoded
    assert "https://api.openai.com" not in encoded


@pytest.mark.parametrize(
    "message",
    [
        "帮我检查这台电脑",
        "run system diagnostics",
        "run computer diagnostics",
    ],
)
def test_natural_language_system_check_runs_auto_routes_to_read_only_diagnostics(monkeypatch, message):
    """Packaged command-dock text must become an OS read-only diagnostics run."""
    _configure_offline_system_check(monkeypatch)
    monkeypatch.setenv("LENGRVIS_DEFAULT_ENGINE", "developer")
    client = TestClient(__import__("app.main", fromlist=["create_app"]).create_app())

    created = client.post(
        "/api/runs",
        json={"message": message, "mode": "privacy", "engine": "auto"},
    )
    assert created.status_code == 200
    run = created.json()
    assert run["engine"] == "os"

    final = _wait_for_run_phase(client, run["run_id"], "completed", "failed", "denied")
    assert final["phase"] == "completed"
    _assert_read_only_system_diagnostics_task(final["task_id"])


@pytest.mark.parametrize(
    "message",
    [
        "帮我检查这台电脑",
        "run system diagnostics",
        "run computer diagnostics",
    ],
)
def test_natural_language_system_check_chat_delegates_to_read_only_diagnostics(monkeypatch, message):
    """The same packaged command-dock text must work when submitted through /api/chat."""
    _configure_offline_system_check(monkeypatch)
    monkeypatch.setenv("LENGRVIS_DEFAULT_ENGINE", "developer")
    client = TestClient(__import__("app.main", fromlist=["create_app"]).create_app())

    response = client.post(
        "/api/chat",
        json={"message": message, "mode": "privacy"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["delegated"] is True
    assert payload["agent"] == "ComputerAgent"
    assert payload["task_id"]

    task = _wait_for_task_status(payload["task_id"], "completed", "failed", "denied")
    assert task["status"] == "completed"
    _assert_read_only_system_diagnostics_task(payload["task_id"])


def test_privacy_mode_offline_document_summary_and_qa_use_deterministic_fallback(monkeypatch, tmp_path):
    """Local documents remain useful in privacy mode before any local LLM is reachable."""
    monkeypatch.setenv("LENGRVIS_MODE", "privacy")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "openai")
    monkeypatch.setenv("LENGRVIS_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-document-secret")
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setattr("app.llm.registry.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    provider_attempts = []

    def _unexpected_provider(*args, **kwargs):  # noqa: ARG001
        provider_attempts.append(True)
        raise AssertionError("privacy document fallback must not construct a cloud or mock provider")

    monkeypatch.setattr("app.llm.registry.OpenAICompatibleProvider", _unexpected_provider)
    monkeypatch.setattr("app.llm.registry.MockProvider", _unexpected_provider)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    document = workspace / "first-run-brief.txt"
    document.write_text(
        "\n\n".join(
            [
                "First-run privacy evidence stays on the local machine.",
                "The deterministic fallback can summarize a readable document without a reachable local LLM.",
                "The QA fallback returns source chunks and citations for local document answers.",
                "Installing a local model remains an explicit setup step.",
            ]
        ),
        encoding="utf-8",
    )
    settings = AppSettings(
        mode="privacy",
        provider_name="openai",
        base_url="https://api.openai.com/v1",
        api_key="sk-document-secret",
        allow_mock_fallback=True,
        allowed_directories=[str(workspace)],
    )
    context = {"allowed_directories": [str(workspace)], "settings": settings}

    summary = document_tools.summarize({"path": str(document)}, context)
    answer = document_tools.qa(
        {"path": str(document), "question": "What does the QA fallback return?"},
        context,
    )

    assert summary["note"] == "extractive_fallback"
    assert "deterministic fallback can summarize" in summary["summary"]
    assert answer["note"] == "extractive_fallback"
    assert answer["citations"] >= 1
    assert answer["citation_labels"]
    assert any("source chunks and citations" in chunk["text"] for chunk in answer["source_chunks"])
    assert provider_attempts == []

    encoded = json.dumps([summary, answer], ensure_ascii=False)
    assert "sk-document-secret" not in encoded
    assert "https://api.openai.com" not in encoded


def test_local_ai_status_failure_redacts_exception_details(monkeypatch):
    """Local AI diagnostics should fail closed without exposing paths, URLs, or tokens."""

    def _failing_health_snapshot(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("sk-local-ai-secret C:\\models\\private\\model.onnx https://api.openai.com/v1")

    monkeypatch.setattr("app.llm.local_provider.health_snapshot", _failing_health_snapshot)

    payload = system_tools.local_ai_status({}, {})

    assert payload["scope"] == "local_only"
    assert payload["available"] is False
    assert payload["error"] == "Local AI readiness check failed."
    assert payload["error_type"] == "RuntimeError"
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "sk-local-ai-secret" not in encoded
    assert "C:\\models\\private" not in encoded
    assert "https://api.openai.com" not in encoded


def test_local_ai_status_redacts_snapshot_error_details(monkeypatch):
    """Provider-reported readiness errors should be useful without leaking local paths or secrets."""

    def _health_snapshot_with_error(*args, **kwargs):  # noqa: ARG001
        return {
            "available": False,
            "selected_backend": {"kind": "ollama", "model": r"C:\models\private\model.onnx"},
            "error": r"failed C:\models\private\model.onnx https://localhost:11434/api?token=secret-token-1234567890 password=abc1234567890",
            "readiness": {
                "can_install": True,
                "recommended_model": r"C:\models\private\other.onnx",
                "reason": r"repair C:\models\private\model.onnx via https://localhost:11434?api_key=sk-local-ai-secret",
            },
        }

    monkeypatch.setattr("app.llm.local_provider.health_snapshot", _health_snapshot_with_error)

    payload = system_tools.local_ai_status({}, {})
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["scope"] == "local_only"
    assert payload["available"] is False
    assert payload["selected_model"] == ""
    assert "[local path]" in payload["error"]
    assert "[url]" in payload["error"]
    assert "C:\\models\\private" not in encoded
    assert "secret-token-1234567890" not in encoded
    assert "abc1234567890" not in encoded
    assert "sk-local-ai-secret" not in encoded
    assert "localhost:11434" not in encoded


def _wait_for_run_phase(client: TestClient, run_id: str, *phases: str) -> dict:
    for _ in range(120):
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["phase"] in phases:
            if payload["phase"] in {"completed", "failed", "denied", "cancelled"}:
                _wait_for_run_inactive(run_id)
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not reach {phases}")


def _wait_for_run_inactive(run_id: str) -> None:
    for _ in range(120):
        if run_id not in run_service.active_run_ids():
            return
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} was still active after reaching a terminal phase")


def _wait_for_task_status(task_id: str, *statuses: str) -> dict:
    for _ in range(120):
        task = db.fetch_one("tasks", task_id)
        if task and task["status"] in statuses:
            return task
        time.sleep(0.05)
    raise AssertionError(f"Task {task_id} did not reach {statuses}")


def _configure_offline_system_check(monkeypatch) -> None:
    monkeypatch.setenv("LENGRVIS_MODE", "privacy")
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "openai")
    monkeypatch.setenv("LENGRVIS_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-natural-language-system-check")
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    monkeypatch.setattr("app.llm.registry.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.registry.detect_local_backend", lambda: None)
    monkeypatch.setattr("app.llm.local_provider.detect_onnx_backend", lambda settings=None: None)
    monkeypatch.setattr("app.llm.local_provider.onnx_health_snapshot", lambda settings=None: {"available": False})
    monkeypatch.setattr("app.llm.local_provider.detect_local_backend", lambda **kwargs: None)

    def _unexpected_provider(*args, **kwargs):  # noqa: ARG001, ANN202
        raise AssertionError("natural-language system check must not construct a cloud or mock provider")

    def _ready_for_local_setup(model=None):
        return {
            "can_install": True,
            "recommended_model": model or "qwen2.5:3b",
            "reason": "ready",
            "checks": [],
        }

    monkeypatch.setattr("app.llm.registry.OpenAICompatibleProvider", _unexpected_provider)
    monkeypatch.setattr("app.llm.registry.MockProvider", _unexpected_provider)
    monkeypatch.setattr("app.agents.planner_agent.get_provider", _unexpected_provider)
    monkeypatch.setattr("app.agents.planner_agent.MockProvider", _unexpected_provider)
    monkeypatch.setattr("app.services.ollama_service.hardware_readiness", _ready_for_local_setup)
    monkeypatch.setattr("app.llm.local_provider.hardware_readiness", _ready_for_local_setup)


def _assert_read_only_system_diagnostics_task(task_id: str) -> None:
    task = db.fetch_one("tasks", task_id)
    assert task["status"] == "completed"
    assert task["execution_stage"] == "idle"
    plans = db.fetch_many("plans", "task_id = ?", (task_id,), limit=1)
    assert plans
    plan = Plan.model_validate(plans[0])
    assert plan.requires_user_approval is False
    assert plan.global_risk_level.value == "R0_READ_ONLY"
    assert [(step.agent_name, step.tool_name, step.status.value, step.risk_level.value) for step in plan.steps] == [
        ("ComputerAgent", "system.diagnostics", "succeeded", "R0_READ_ONLY")
    ]
    assert db.fetch_many("approvals", "task_id = ?", (task_id,), limit=10) == []
    tool_calls = db.fetch_many("tool_calls", "task_id = ?", (task_id,), limit=10)
    assert [call["tool_name"] for call in tool_calls] == ["system.diagnostics"]
    results = []
    for call in tool_calls:
        results.extend(db.fetch_many("tool_results", "tool_call_id = ?", (call["id"],), limit=1))
    assert results
    assert results[0]["ok"] is True
    output = results[0]["output"]
    assert output["local_ai"]["scope"] == "local_only"
    assert output["local_ai"]["available"] is False
    assert output["local_ai"]["readiness"]["can_install"] is True
    encoded = json.dumps([task, plan.model_dump(mode="json"), output], ensure_ascii=False)
    assert "sk-natural-language-system-check" not in encoded
    assert "https://api.openai.com" not in encoded
