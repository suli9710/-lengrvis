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
    assert names[1] == "golden-gate"
    assert "signed-artifacts" in names
    assert names.index("release-safety") < names.index("signed-artifacts")
    assert names.index("signed-artifacts") < names.index("market-readiness")
    assert names.index("supply-chain") < names.index("dependency-audit")
    assert names.index("dependency-audit") < names.index("secret-scan")
    assert names.index("secret-scan") < names.index("security-extensions")
    assert "market-readiness" in names
    assert "readiness" in names
    assert "real-llm-eval" not in names
    assert names[-1] == "evidence"
    # Evidence is the only optional stage by default.
    optional = [s.name for s in stages if not s.required]
    assert optional == ["evidence"]
    signed = next(s for s in stages if s.name == "signed-artifacts")
    assert signed.required is True
    assert "verify:windows-release-signatures" in signed.command


def test_non_strict_skip_signature_verify_omits_stage():
    stages = mod.default_stages(strict=False, skip_signature_verify=True)
    names = [s.name for s in stages]
    assert "signed-artifacts" not in names
    assert "release-artifact-preflight" not in names
    assert names[-1] == "evidence"


def test_non_strict_includes_release_preflight_before_signed_artifacts():
    stages = mod.default_stages(strict=False)
    names = [s.name for s in stages]
    assert "release-artifact-preflight" in names
    assert "signed-artifacts" in names
    assert names.index("release-artifact-preflight") < names.index("signed-artifacts")
    assert names.index("signed-artifacts") < names.index("market-readiness")


def test_strict_always_includes_signed_artifacts_even_when_skip_requested():
    stages = mod.default_stages(strict=True, skip_signature_verify=True)
    names = [s.name for s in stages]
    assert "signed-artifacts" in names


def test_build_signature_verify_warnings():
    effective, warnings = mod.build_signature_verify_warnings(
        strict=False,
        skip_signature_verify_requested=True,
    )
    assert effective is True
    assert len(warnings) == 1
    assert "skipped via --skip-signature-verify" in warnings[0]

    effective, warnings = mod.build_signature_verify_warnings(
        strict=True,
        skip_signature_verify_requested=True,
    )
    assert effective is False
    assert len(warnings) == 1
    assert "ignored in strict RC mode" in warnings[0]

    effective, warnings = mod.build_signature_verify_warnings(
        strict=False,
        skip_signature_verify_requested=False,
    )
    assert effective is False
    assert warnings == []


def test_strict_adds_strict_flag_to_readiness():
    names = [s.name for s in mod.default_stages(strict=True)]
    assert names == [
        "qa-gate",
        "golden-gate",
        "real-llm-eval",
        "supply-chain",
        "dependency-audit",
        "secret-scan",
        "security-extensions",
        "release-safety",
        "packaging-verify",
        "signed-artifacts",
        "distribution-evidence",
        "clean-machine-evidence",
        "result-quality-evidence",
        "android-strict-gate",
        "commercial-loop",
        "market-readiness",
        "readiness",
        "evidence",
    ]
    readiness = next(s for s in mod.default_stages(strict=True) if s.name == "readiness")
    assert "--rc-release" in readiness.command
    assert "--strict" not in readiness.command
    market = next(s for s in mod.default_stages(strict=True) if s.name == "market-readiness")
    assert "--strict" in market.command
    assert "--paid-launch" not in market.command
    android = next(s for s in mod.default_stages(strict=True) if s.name == "android-strict-gate")
    assert "LENGRVIS_ANDROID_APK_PATH" in android.command[-1]
    assert "LENGRVIS_ANDROID_REAL_DEVICE_EVIDENCE_PATH" in android.command[-1]


def test_paid_launch_adds_commercial_evidence_and_paid_market_gate():
    stages = mod.default_stages(strict=True, paid_launch=True)
    names = [s.name for s in stages]
    assert "support-privacy-evidence" in names
    assert "claims-launch-evidence" in names
    assert "commercial-operations-evidence" in names
    assert names.index("commercial-loop") < names.index("support-privacy-evidence")
    assert names.index("support-privacy-evidence") < names.index("claims-launch-evidence")
    assert names.index("claims-launch-evidence") < names.index("commercial-operations-evidence")
    assert names.index("commercial-operations-evidence") < names.index("market-readiness")
    market = next(s for s in stages if s.name == "market-readiness")
    assert "--paid-launch" in market.command
    assert "--strict" not in market.command
    readiness = next(s for s in stages if s.name == "readiness")
    assert "--rc-release" in readiness.command


def test_non_strict_readiness_has_no_strict_flag():
    readiness = next(s for s in mod.default_stages(strict=False) if s.name == "readiness")
    assert "--strict" not in readiness.command
    market = next(s for s in mod.default_stages(strict=False) if s.name == "market-readiness")
    assert "--strict" not in market.command


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
