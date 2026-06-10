"""真实 LLM 评测轨道 B harness 的契约测试（不调用任何真实 LLM）。"""
from __future__ import annotations

import importlib.util
import sys
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
        },
        {
            "ran": True,
            "error": "",
            "phase_ok": False,
            "intent_exact_match": False,
            "expected_tools_planned": True,
            "risk_match": None,
            "actual_plan_tools": ["file.search_by_name"],
            "param_missing": [{"tool": "file.search_by_name", "missing": ["query"]}],
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


def test_required_args_missing_flags_unknown_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("LENGRVIS_CONFIG_FILE", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("LENGRVIS_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("LENGRVIS_DATA_DIR", str(tmp_path / "data"))
    harness = _load_harness()

    missing = harness._required_args_missing(
        [{"tool_name": "definitely.not.a.tool", "args": {}}]
    )

    assert missing and missing[0]["missing"] == ["<unknown tool>"]
