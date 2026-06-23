"""Tests for scripts/delivery_pipeline.py.

The orchestrator lives under scripts/ (not on the backend import path), so we load
it by file path with importlib and exercise the pure policy helpers.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "delivery_pipeline.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("delivery_pipeline", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec_module: on Python 3.12/3.13 dataclasses resolves a
    # frozen dataclass's (stringized) annotations via sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def test_script_path_exists():
    assert SCRIPT_PATH.exists(), f"missing orchestrator at {SCRIPT_PATH}"


def test_default_stages_order_and_membership():
    stages = mod.default_stages(strict=False)
    names = [s.name for s in stages]
    assert names[0] == "qa-gate"
    assert "readiness" in names
    assert names[-1] == "evidence"
    # Evidence is the only optional stage by default.
    optional = [s.name for s in stages if not s.required]
    assert optional == ["evidence"]


def test_strict_adds_strict_flag_to_readiness():
    readiness = next(s for s in mod.default_stages(strict=True) if s.name == "readiness")
    assert "--strict" in readiness.command


def test_non_strict_readiness_has_no_strict_flag():
    readiness = next(s for s in mod.default_stages(strict=False) if s.name == "readiness")
    assert "--strict" not in readiness.command


def test_build_plan_shape():
    plan = mod.build_plan(mod.default_stages(strict=False))
    assert plan and all({"name", "command", "required", "description"} <= set(row) for row in plan)


def test_aggregate_blocks_on_required_failure():
    results = [
        mod.StageResult("qa-gate", True, "failed", 1, 0.1),
        mod.StageResult("readiness", True, "skipped"),
    ]
    verdict = mod.aggregate_verdict(results)
    assert verdict["ok"] is False
    assert verdict["decision"] == "blocked"
    assert "qa-gate" in verdict["required_failures"]
    assert "readiness" in verdict["skipped"]


def test_aggregate_passes_when_only_optional_fails():
    results = [
        mod.StageResult("qa-gate", True, "passed", 0, 0.1),
        mod.StageResult("evidence", False, "failed", 2, 0.1),
    ]
    verdict = mod.aggregate_verdict(results)
    assert verdict["ok"] is True
    assert verdict["decision"] == "passed"
    assert "evidence" in verdict["optional_failures"]


def test_run_pipeline_halts_after_required_failure():
    stages = [
        mod.Stage("a", ["true"], True),
        mod.Stage("b", ["false"], True),
        mod.Stage("c", ["true"], True),
    ]

    def fake_run_stage(stage, *, cwd):
        status = "passed" if stage.name == "a" else "failed"
        return mod.StageResult(stage.name, stage.required, status, 0 if status == "passed" else 1)

    original = mod.run_stage
    mod.run_stage = fake_run_stage  # type: ignore[assignment]
    try:
        results = mod.run_pipeline(stages, cwd=REPO_ROOT, keep_going=False)
    finally:
        mod.run_stage = original  # type: ignore[assignment]

    by_name = {r.name: r for r in results}
    assert by_name["a"].status == "passed"
    assert by_name["b"].status == "failed"
    # c must be skipped because b (required) failed and keep_going is False.
    assert by_name["c"].status == "skipped"


def test_run_pipeline_keep_going_runs_all():
    stages = [
        mod.Stage("a", ["true"], True),
        mod.Stage("b", ["false"], True),
        mod.Stage("c", ["true"], True),
    ]

    def fake_run_stage(stage, *, cwd):
        status = "failed" if stage.name == "b" else "passed"
        return mod.StageResult(stage.name, stage.required, status, 1 if status == "failed" else 0)

    original = mod.run_stage
    mod.run_stage = fake_run_stage  # type: ignore[assignment]
    try:
        results = mod.run_pipeline(stages, cwd=REPO_ROOT, keep_going=True)
    finally:
        mod.run_stage = original  # type: ignore[assignment]

    assert [r.status for r in results] == ["passed", "failed", "passed"]
