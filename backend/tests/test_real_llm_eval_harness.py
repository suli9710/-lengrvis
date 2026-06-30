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

    with pytest.raises(SystemExit, match="mock"):
        harness._require_real_provider()


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
    assert summary["task_success_rate"] == 0.5
    assert summary["intent_accuracy"] == 0.5
    assert summary["tool_overlap_rate"] == 1.0
    assert summary["risk_match_rate"] == 1.0
    assert summary["param_missing_rate"] == 0.5
    assert summary["structured_failure_rate"] == 0.3333
    assert summary["plan_schema_valid_rate"] == 1.0
    assert summary["unknown_tool_rate"] == 0.5


def test_quality_gate_blocks_low_real_llm_metrics():
    harness = _load_harness()
    args = Namespace(
        quality_gate=True,
        min_task_success_rate=0.8,
        min_intent_accuracy=0.7,
        min_tool_overlap_rate=0.8,
        min_risk_match_rate=0.8,
        max_param_missing_rate=0.05,
        max_structured_failure_rate=0.0,
        max_unknown_tool_rate=0.0,
    )

    gate = harness._quality_gate(
        {
            "tasks_ran": 2,
            "tasks_errored": 1,
            "task_success_rate": 0.5,
            "intent_accuracy": 0.5,
            "tool_overlap_rate": 1.0,
            "risk_match_rate": 1.0,
            "param_missing_rate": 0.5,
            "structured_failure_rate": 0.0,
            "plan_schema_valid_rate": 1.0,
            "unknown_tool_rate": 0.0,
        },
        args,
    )

    assert gate["enabled"] is True
    assert gate["passed"] is False
    assert any("task_success_rate" in failure for failure in gate["failures"])
    assert any("param_missing_rate" in failure for failure in gate["failures"])


def test_quality_gate_blocks_structured_failure_and_unknown_tools():
    harness = _load_harness()
    args = Namespace(
        quality_gate=True,
        min_task_success_rate=0.8,
        min_intent_accuracy=0.7,
        min_tool_overlap_rate=0.8,
        min_risk_match_rate=0.8,
        max_param_missing_rate=0.05,
        max_structured_failure_rate=0.0,
        max_unknown_tool_rate=0.0,
    )

    gate = harness._quality_gate(
        {
            "tasks_ran": 3,
            "tasks_errored": 0,
            "task_success_rate": 1.0,
            "intent_accuracy": 1.0,
            "tool_overlap_rate": 1.0,
            "risk_match_rate": 1.0,
            "param_missing_rate": 0.0,
            "structured_failure_rate": 0.25,
            "plan_schema_valid_rate": 1.0,
            "unknown_tool_rate": 0.25,
        },
        args,
    )

    assert gate["enabled"] is True
    assert gate["passed"] is False
    assert any("structured_failure_rate" in failure for failure in gate["failures"])
    assert any("unknown_tool_rate" in failure for failure in gate["failures"])


def test_required_args_missing_flags_unknown_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    harness = _load_harness()

    missing = harness._required_args_missing([{"tool_name": "definitely.not.a.tool", "args": {}}])

    assert missing and missing[0]["missing"] == ["<unknown tool>"]
