"""真实 LLM 评测轨道 B harness 的契约测试（不调用任何真实 LLM）。"""

from __future__ import annotations

import importlib.util
import sys
from argparse import Namespace
from pathlib import Path

import pytest

HARNESS_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_real_llm_eval.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("run_real_llm_eval", HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("run_real_llm_eval", module)
    spec.loader.exec_module(module)
    return module


def test_harness_refuses_mock_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "mock")
    harness = _load_harness()

    with pytest.raises(SystemExit) as exc_info:
        harness._require_real_provider()
    message = str(exc_info.value)
    assert "provider_name=mock" in message
    assert "real provider preflight failed" not in message


def test_harness_refuses_resolved_mock_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "openai_compatible")
    monkeypatch.setenv("LENGRVIS_API_KEY", "")
    monkeypatch.setenv("LENGRVIS_ALLOW_MOCK_FALLBACK", "true")
    harness = _load_harness()

    with pytest.raises(SystemExit, match="MockProvider"):
        harness._require_real_provider()


def test_harness_reports_private_cloud_base_url_without_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LENGRVIS_PROVIDER_NAME", "openai_compatible")
    monkeypatch.setenv("LENGRVIS_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("LENGRVIS_API_KEY", "sk-test-secret-real-llm-eval")
    monkeypatch.setenv("LENGRVIS_MODE", "efficiency")
    harness = _load_harness()

    with pytest.raises(SystemExit) as exc_info:
        harness._require_real_provider()

    message = str(exc_info.value)
    assert "real provider preflight failed" in message
    assert "non-private cloud/OpenAI-compatible base URL" in message
    assert "LENGRVIS_BASE_URL" in message
    assert "SSRF guard" in message
    assert "127.0.0.1" not in message
    assert "sk-test-secret-real-llm-eval" not in message


def test_harness_reports_blank_cloud_base_url_without_secrets(monkeypatch):
    harness = _load_harness()
    import app.llm.registry as registry
    from app.config import AppSettings

    settings = AppSettings(
        provider_name="openai_compatible",
        base_url="",
        api_key="sk-test-secret-real-llm-eval",
        mode="efficiency",
    )
    monkeypatch.setattr(registry, "get_effective_settings", lambda: settings)

    with pytest.raises(SystemExit) as exc_info:
        harness._require_real_provider()

    message = str(exc_info.value)
    assert "configured base URL is required" in message
    assert "real provider preflight failed" in message
    assert "sk-test-secret-real-llm-eval" not in message


def test_harness_reports_local_provider_failure_without_cloud_guidance_or_values(monkeypatch):
    harness = _load_harness()
    import app.llm.registry as registry
    from app.config import AppSettings
    from app.llm.local_provider import LocalBackendUnavailable

    settings = AppSettings(
        provider_name="ollama",
        base_url="http://127.0.0.1:11434/v1",
        api_key="sk-test-secret-real-llm-eval",
        model="qwen2.5:3b-instruct",
        mode="privacy",
    )

    def fail_provider(settings, task="default"):  # noqa: ARG001
        raise LocalBackendUnavailable(
            "Privacy mode requires a reachable local LLM backend. "
            "Tried ollama (http://127.0.0.1:11434/api/tags)."
        )

    monkeypatch.setattr(registry, "get_effective_settings", lambda: settings)
    monkeypatch.setattr(registry, "get_provider_for_mode", fail_provider)

    with pytest.raises(SystemExit) as exc_info:
        harness._require_real_provider()

    message = str(exc_info.value)
    assert "local provider preflight failed" in message
    assert "no reachable local LLM backend was detected" in message
    assert "LENGRVIS_PROVIDER_NAME=ollama/lmstudio/llamacpp/onnx" in message
    assert "LENGRVIS_MODE=privacy" in message
    assert "non-private cloud/OpenAI-compatible base URL" not in message
    assert "API key" not in message
    assert "SSRF guard" not in message
    assert "127.0.0.1" not in message
    assert "11434" not in message
    assert "sk-test-secret-real-llm-eval" not in message


def test_harness_reports_privacy_openai_compatible_local_failure_without_cloud_guidance_or_values(
    monkeypatch,
):
    harness = _load_harness()
    import app.llm.registry as registry
    from app.config import AppSettings
    from app.llm.local_provider import LocalBackendUnavailable

    settings = AppSettings(
        provider_name="openai_compatible",
        base_url="http://127.0.0.1:11434/v1",
        api_key="sk-test-secret-real-llm-eval",
        model="qwen2.5:3b-instruct",
        mode="privacy",
    )

    def fail_provider(settings, task="default"):  # noqa: ARG001
        raise LocalBackendUnavailable(
            "Privacy mode requires a reachable local LLM backend. "
            "Tried local OpenAI-compatible backend (http://127.0.0.1:11434/v1)."
        )

    monkeypatch.setattr(registry, "get_effective_settings", lambda: settings)
    monkeypatch.setattr(registry, "get_provider_for_mode", fail_provider)

    with pytest.raises(SystemExit) as exc_info:
        harness._require_real_provider()

    message = str(exc_info.value)
    assert "local provider preflight failed" in message
    assert "no reachable local LLM backend was detected" in message
    assert "LENGRVIS_MODE=privacy" in message
    assert "non-private cloud/OpenAI-compatible base URL" not in message
    assert "API key" not in message
    assert "SSRF guard" not in message
    assert "cloud provider is missing an API key" not in message
    assert "127.0.0.1" not in message
    assert "11434" not in message
    assert "sk-test-secret-real-llm-eval" not in message


def test_harness_keeps_cloud_provider_failures_on_cloud_guidance(monkeypatch):
    harness = _load_harness()
    import app.llm.registry as registry
    from app.config import AppSettings
    from app.llm.local_provider import LocalBackendUnavailable

    settings = AppSettings(
        provider_name="openai_compatible",
        base_url="https://cloud-provider.example/v1",
        api_key="sk-test-secret-real-llm-eval",
        mode="efficiency",
    )

    def fail_provider(settings, task="default"):  # noqa: ARG001
        raise LocalBackendUnavailable("cloud provider without api_key")

    monkeypatch.setattr(harness, "_validate_real_provider_preflight", lambda settings: None)
    monkeypatch.setattr(registry, "get_effective_settings", lambda: settings)
    monkeypatch.setattr(registry, "get_provider_for_mode", fail_provider)

    with pytest.raises(SystemExit) as exc_info:
        harness._require_real_provider()

    message = str(exc_info.value)
    assert "real provider preflight failed" in message
    assert "cloud provider is missing an API key" in message
    assert "non-private cloud/OpenAI-compatible base URL" in message
    assert "cloud-provider.example" not in message
    assert "sk-test-secret-real-llm-eval" not in message


def test_provider_config_reason_prefers_unresolvable_dns_over_ssrf():
    harness = _load_harness()

    reason = harness._provider_config_failure_reason(
        ValueError(
            "Outbound URL hostname could not be resolved; refusing connect to prevent SSRF."
        )
    )

    assert reason == "configured base URL hostname could not be resolved"


def test_harness_does_not_mask_unexpected_settings_value_error(monkeypatch):
    harness = _load_harness()
    import app.llm.registry as registry

    def fail_settings():
        raise ValueError("unexpected settings validation bug")

    monkeypatch.setattr(registry, "get_effective_settings", fail_settings)

    with pytest.raises(ValueError, match="unexpected settings validation bug"):
        harness._require_real_provider()


def test_aggregate_metrics_math():
    harness = _load_harness()
    records = [
        {
            "ran": True,
            "error": "",
            "phase_ok": True,
            "intent_exact_match": True,
            "expected_tools_planned": True,
            "risk_match": True,
            "actual_plan_tools": ["system.diagnostics"],
            "param_missing": [],
            "structured_failure_kind": "",
            "plan_schema_valid": True,
            "unknown_tool_count": 0,
        },
        {
            "ran": True,
            "error": "",
            "phase_ok": False,
            "intent_exact_match": False,
            "expected_tools_planned": True,
            "risk_match": None,
            "actual_plan_tools": ["definitely.not.a.tool"],
            "param_missing": [{"tool": "definitely.not.a.tool", "missing": ["<unknown tool>"]}],
            "structured_failure_kind": "",
            "plan_schema_valid": True,
            "unknown_tool_count": 1,
        },
        {
            "ran": False,
            "error": "RuntimeError: boom",
            "phase_ok": None,
            "intent_exact_match": None,
            "expected_tools_planned": None,
            "risk_match": None,
            "actual_plan_tools": [],
            "param_missing": [],
            "structured_failure_kind": "not_json",
            "plan_schema_valid": None,
            "unknown_tool_count": 0,
        },
    ]

    summary = harness._aggregate(records)

    assert summary["tasks_total"] == 3
    assert summary["tasks_ran"] == 2
    assert summary["tasks_errored"] == 1
    assert summary["task_success_count"] == 1
    assert summary["task_success_denominator"] == 2
    assert summary["task_success_rate"] == 0.5
    assert summary["intent_accuracy_count"] == 1
    assert summary["intent_accuracy_denominator"] == 2
    assert summary["intent_accuracy"] == 0.5
    assert summary["tool_overlap_count"] == 2
    assert summary["tool_overlap_denominator"] == 2
    assert summary["tool_overlap_rate"] == 1.0
    assert summary["risk_match_count"] == 1
    assert summary["risk_match_denominator"] == 1
    assert summary["risk_match_rate"] == 1.0
    assert summary["param_missing_count"] == 1
    assert summary["param_missing_denominator"] == 2
    assert summary["param_missing_rate"] == 0.5
    assert summary["structured_failure_count"] == 1
    assert summary["structured_failure_denominator"] == 3
    assert summary["structured_failure_rate"] == 0.3333
    assert summary["plan_schema_valid_count"] == 2
    assert summary["plan_schema_valid_denominator"] == 2
    assert summary["plan_schema_valid_rate"] == 1.0
    assert summary["unknown_tool_count"] == 1
    assert summary["unknown_tool_denominator"] == 2
    assert summary["unknown_tool_rate"] == 0.5


def test_quality_gate_blocks_low_real_llm_metrics():
    harness = _load_harness()
    args = Namespace(
        quality_gate=True,
        min_task_success_rate=0.8,
        min_intent_accuracy=0.7,
        min_tool_overlap_rate=0.8,
        min_risk_match_rate=0.8,
        min_task_count=20,
        min_task_success_count=18,
        min_intent_accuracy_count=14,
        min_tool_overlap_count=14,
        min_risk_match_count=9,
        min_param_missing_count=14,
        min_structured_failure_count=20,
        min_unknown_tool_count=14,
        max_param_missing_rate=0.05,
        max_structured_failure_rate=0.0,
        max_unknown_tool_rate=0.0,
    )

    gate = harness._quality_gate(
        {
            "tasks_ran": 2,
            "tasks_errored": 1,
            "task_success_denominator": 2,
            "task_success_rate": 0.5,
            "intent_accuracy_denominator": 2,
            "intent_accuracy": 0.5,
            "tool_overlap_denominator": 2,
            "tool_overlap_rate": 1.0,
            "risk_match_denominator": 1,
            "risk_match_rate": 1.0,
            "param_missing_denominator": 2,
            "param_missing_rate": 0.5,
            "structured_failure_denominator": 3,
            "structured_failure_rate": 0.0,
            "plan_schema_valid_rate": 1.0,
            "unknown_tool_denominator": 2,
            "unknown_tool_rate": 0.0,
        },
        args,
    )

    assert gate["enabled"] is True
    assert gate["passed"] is False
    assert any("task_success_rate" in failure for failure in gate["failures"])
    assert any("tasks_ran=2 below release threshold 20" in failure for failure in gate["failures"])
    assert any("risk_match_rate denominator=1 below release threshold 9" in failure for failure in gate["failures"])
    assert any("param_missing_rate" in failure for failure in gate["failures"])


def test_quality_gate_blocks_structured_failure_and_unknown_tools():
    harness = _load_harness()
    args = Namespace(
        quality_gate=True,
        min_task_success_rate=0.8,
        min_intent_accuracy=0.7,
        min_tool_overlap_rate=0.8,
        min_risk_match_rate=0.8,
        min_task_count=3,
        min_task_success_count=3,
        min_intent_accuracy_count=3,
        min_tool_overlap_count=3,
        min_risk_match_count=3,
        min_param_missing_count=3,
        min_structured_failure_count=3,
        min_unknown_tool_count=3,
        max_param_missing_rate=0.05,
        max_structured_failure_rate=0.0,
        max_unknown_tool_rate=0.0,
    )

    gate = harness._quality_gate(
        {
            "tasks_ran": 3,
            "tasks_errored": 0,
            "task_success_denominator": 3,
            "task_success_rate": 1.0,
            "intent_accuracy_denominator": 3,
            "intent_accuracy": 1.0,
            "tool_overlap_denominator": 3,
            "tool_overlap_rate": 1.0,
            "risk_match_denominator": 3,
            "risk_match_rate": 1.0,
            "param_missing_denominator": 3,
            "param_missing_rate": 0.0,
            "structured_failure_denominator": 3,
            "structured_failure_rate": 0.25,
            "plan_schema_valid_rate": 1.0,
            "unknown_tool_denominator": 3,
            "unknown_tool_rate": 0.25,
        },
        args,
    )

    assert gate["enabled"] is True
    assert gate["passed"] is False
    assert any("structured_failure_rate" in failure for failure in gate["failures"])
    assert any("unknown_tool_rate" in failure for failure in gate["failures"])


def test_quality_gate_blocks_too_small_real_llm_sample_even_when_metrics_pass():
    harness = _load_harness()
    args = Namespace(
        quality_gate=True,
        min_task_success_rate=0.8,
        min_intent_accuracy=0.7,
        min_tool_overlap_rate=0.8,
        min_risk_match_rate=0.8,
        min_task_count=20,
        min_task_success_count=10,
        min_intent_accuracy_count=10,
        min_tool_overlap_count=10,
        min_risk_match_count=10,
        min_param_missing_count=10,
        min_structured_failure_count=10,
        min_unknown_tool_count=10,
        max_param_missing_rate=0.05,
        max_structured_failure_rate=0.0,
        max_unknown_tool_rate=0.0,
    )

    gate = harness._quality_gate(
        {
            "tasks_ran": 10,
            "tasks_errored": 0,
            "task_success_denominator": 10,
            "task_success_rate": 1.0,
            "intent_accuracy_denominator": 10,
            "intent_accuracy": 1.0,
            "tool_overlap_denominator": 10,
            "tool_overlap_rate": 1.0,
            "risk_match_denominator": 10,
            "risk_match_rate": 1.0,
            "param_missing_denominator": 10,
            "param_missing_rate": 0.0,
            "structured_failure_denominator": 10,
            "structured_failure_rate": 0.0,
            "plan_schema_valid_rate": 1.0,
            "unknown_tool_denominator": 10,
            "unknown_tool_rate": 0.0,
        },
        args,
    )

    assert gate["enabled"] is True
    assert gate["passed"] is False
    assert gate["thresholds"]["min_task_count"] == 20
    assert gate["failures"] == ["tasks_ran=10 below release threshold 20"]


def test_quality_gate_blocks_small_metric_denominator_even_when_rates_pass():
    harness = _load_harness()
    args = Namespace(
        quality_gate=True,
        min_task_success_rate=0.8,
        min_intent_accuracy=0.7,
        min_tool_overlap_rate=0.8,
        min_risk_match_rate=0.8,
        min_task_count=20,
        min_task_success_count=18,
        min_intent_accuracy_count=14,
        min_tool_overlap_count=14,
        min_risk_match_count=9,
        min_param_missing_count=14,
        min_structured_failure_count=20,
        min_unknown_tool_count=14,
        max_param_missing_rate=0.05,
        max_structured_failure_rate=0.0,
        max_unknown_tool_rate=0.0,
    )

    gate = harness._quality_gate(
        {
            "tasks_ran": 20,
            "tasks_errored": 0,
            "task_success_denominator": 18,
            "task_success_rate": 1.0,
            "intent_accuracy_denominator": 10,
            "intent_accuracy": 1.0,
            "tool_overlap_denominator": 10,
            "tool_overlap_rate": 1.0,
            "risk_match_denominator": 5,
            "risk_match_rate": 1.0,
            "param_missing_denominator": 10,
            "param_missing_rate": 0.0,
            "structured_failure_denominator": 20,
            "structured_failure_rate": 0.0,
            "plan_schema_valid_rate": 1.0,
            "unknown_tool_denominator": 10,
            "unknown_tool_rate": 0.0,
        },
        args,
    )

    assert gate["passed"] is False
    assert any("intent_accuracy denominator=10 below release threshold 14" in failure for failure in gate["failures"])
    assert any("risk_match_rate denominator=5 below release threshold 9" in failure for failure in gate["failures"])


def test_quality_gate_disabled_does_not_report_threshold_failures():
    harness = _load_harness()
    args = Namespace(
        quality_gate=False,
        min_task_success_rate=0.8,
        min_intent_accuracy=0.7,
        min_tool_overlap_rate=0.8,
        min_risk_match_rate=0.8,
        min_task_count=20,
        min_task_success_count=18,
        min_intent_accuracy_count=14,
        min_tool_overlap_count=14,
        min_risk_match_count=9,
        min_param_missing_count=14,
        min_structured_failure_count=20,
        min_unknown_tool_count=14,
        max_param_missing_rate=0.05,
        max_structured_failure_rate=0.0,
        max_unknown_tool_rate=0.0,
    )

    gate = harness._quality_gate(
        {
            "tasks_ran": 1,
            "tasks_errored": 0,
            "task_success_denominator": 1,
            "task_success_rate": 0.0,
            "intent_accuracy_denominator": 0,
            "intent_accuracy": None,
            "tool_overlap_denominator": 0,
            "tool_overlap_rate": None,
            "risk_match_denominator": 0,
            "risk_match_rate": None,
            "param_missing_denominator": 0,
            "param_missing_rate": None,
            "structured_failure_denominator": 1,
            "structured_failure_rate": 0.0,
            "plan_schema_valid_rate": None,
            "unknown_tool_denominator": 0,
            "unknown_tool_rate": None,
        },
        args,
    )

    assert gate["enabled"] is False
    assert gate["passed"] is None
    assert gate["failures"] == []


def test_required_args_missing_flags_unknown_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    harness = _load_harness()

    missing = harness._required_args_missing([{"tool_name": "definitely.not.a.tool", "args": {}}])

    assert missing and missing[0]["missing"] == ["<unknown tool>"]
