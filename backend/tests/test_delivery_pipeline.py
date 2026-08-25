"""Tests for scripts/delivery_pipeline.py.

The orchestrator lives under scripts/ (not on the backend import path), so we load
it by file path with importlib and exercise the pure policy helpers.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

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
    assert names[2] == "mcp-conformance"
    assert names[3] == "maintainability-gate"
    assert names[4] == "review-scorecard"
    assert names[5] == "agentic-threat-model"
    assert "signed-artifacts" in names
    assert names.index("release-safety") < names.index("signed-artifacts")
    assert names.index("signed-artifacts") < names.index("market-readiness")
    assert names.index("maintainability-gate") < names.index("supply-chain")
    assert names.index("review-scorecard") < names.index("supply-chain")
    assert names.index("agentic-threat-model") < names.index("supply-chain")
    assert names.index("supply-chain") < names.index("dependency-audit")
    assert names.index("dependency-audit") < names.index("secret-scan")
    assert names.index("secret-scan") < names.index("security-extensions")
    assert "market-readiness" in names
    assert "readiness" in names
    assert names.index("current-release-evidence") < names.index("readiness")
    assert "real-llm-eval" not in names
    assert names[-1] == "evidence"
    # Evidence collection stages are optional outside strict RC mode.
    optional = [s.name for s in stages if not s.required]
    assert optional == ["current-release-evidence", "evidence"]
    signed = next(s for s in stages if s.name == "signed-artifacts")
    assert signed.required is True
    assert "verify:windows-release-signatures" in signed.command
    release_safety = next(s for s in stages if s.name == "release-safety")
    assert release_safety.required is True
    assert "execution-isolation" in release_safety.description
    mcp = next(s for s in stages if s.name == "mcp-conformance")
    assert mcp.environment_policy == mod.ENVIRONMENT_MCP_CONFORMANCE
    assert mcp.command == ["npm", "run", "mcp:conformance"]
    assert mcp.timeout_seconds == 300


def test_delivery_pipeline_docs_track_review_scorecard_gate():
    delivery_doc = (REPO_ROOT / "docs" / "release" / "delivery-pipeline.md").read_text(encoding="utf-8")
    release_gate = (REPO_ROOT / "docs" / "qa" / "release-gate.md").read_text(encoding="utf-8")

    assert "`npm run review:scorecard` verifies the full-review scorecard before any" in delivery_doc
    assert "CI should run `delivery:plan`, `npm run review:scorecard`" in delivery_doc
    assert (
        "candidate-bound MCP and real-LLM quality evidence, maintainability gate, `review:scorecard`"
    ) in release_gate
    assert "agentic-threat-model" in delivery_doc
    assert "security:threat-model" in release_gate


def test_release_workflows_isolate_quality_producers_and_clean_sealers():
    candidate_path = REPO_ROOT / ".github" / "workflows" / "release-candidate.yml"
    publish_path = REPO_ROOT / ".github" / "workflows" / "release-publish.yml"
    reviewed_path = REPO_ROOT / ".github" / "workflows" / "release-reviewed-evidence.yml"
    candidate = yaml.safe_load(candidate_path.read_text(encoding="utf-8"))
    reviewed = yaml.safe_load(reviewed_path.read_text(encoding="utf-8"))
    publish_text = publish_path.read_text(encoding="utf-8")

    mcp_producer = candidate["jobs"]["mcp-conformance"]
    assert mcp_producer["permissions"] == {"contents": "read"}
    assert "environment" not in mcp_producer
    assert mcp_producer["timeout-minutes"] == 20
    mcp_producer_text = json.dumps(mcp_producer, sort_keys=True)
    assert "secrets." not in mcp_producer_text
    assert "id-token" not in mcp_producer_text
    assert "npm run mcp:conformance" in mcp_producer_text
    assert "mcp_conformance_evidence.py emit" not in mcp_producer_text
    assert "mcp-conformance-raw-${{ github.run_id }}-${{ github.run_attempt }}" in mcp_producer_text
    assert {
        str(step["with"]["node-version"])
        for step in mcp_producer["steps"]
        if "actions/setup-node@" in str(step.get("uses", ""))
    } == {"24.11.1"}

    mcp_sealer = candidate["jobs"]["mcp-conformance-evidence"]
    assert mcp_sealer["needs"] == "mcp-conformance"
    assert mcp_sealer["permissions"] == {"contents": "read"}
    assert "environment" not in mcp_sealer
    mcp_sealer_text = json.dumps(mcp_sealer, sort_keys=True)
    assert "secrets." not in mcp_sealer_text
    assert "mcp_conformance_evidence.py emit" in mcp_sealer_text
    assert "npm run mcp:conformance" not in mcp_sealer_text
    assert "npm ci" not in mcp_sealer_text
    assert "pip install" not in mcp_sealer_text
    assert {
        str(step["with"]["node-version"])
        for step in mcp_sealer["steps"]
        if "actions/setup-node@" in str(step.get("uses", ""))
    } == {"24.11.1"}

    real_producer = candidate["jobs"]["real-llm-quality"]
    assert real_producer["environment"] == "release-candidate"
    real_producer_text = json.dumps(real_producer, sort_keys=True)
    assert "--quality-gate --release-evidence" in real_producer_text
    assert "real_llm_evidence.py emit" not in real_producer_text
    assert "real-llm-quality-raw-${{ github.run_id }}-${{ github.run_attempt }}" in real_producer_text
    eval_step = next(step for step in real_producer["steps"] if step.get("name") == "Run real-provider quality gate")
    assert eval_step["env"]["LENGRVIS_API_KEY"] == "${{ secrets.LENGRVIS_REAL_LLM_API_KEY }}"
    assert "LENGRVIS_API_KEY" not in real_producer.get("env", {})
    for step in real_producer["steps"]:
        if step is not eval_step:
            assert "LENGRVIS_API_KEY" not in step.get("env", {})

    real_sealer = candidate["jobs"]["real-llm-evidence"]
    assert real_sealer["needs"] == "real-llm-quality"
    assert real_sealer["permissions"] == {"contents": "read"}
    assert "environment" not in real_sealer
    real_sealer_text = json.dumps(real_sealer, sort_keys=True)
    assert "secrets." not in real_sealer_text
    assert "real_llm_evidence.py emit" in real_sealer_text
    assert "run_real_llm_eval.py" not in real_sealer_text
    assert "pip install" not in real_sealer_text
    assert "npm ci" not in real_sealer_text
    sealed_upload = next(
        step for step in real_sealer["steps"] if step.get("name") == "Upload sealed real LLM quality evidence"
    )
    assert sealed_upload["with"]["retention-days"] == 30
    mcp_upload = next(
        step for step in mcp_sealer["steps"] if step.get("name") == "Upload sealed MCP conformance evidence"
    )
    assert mcp_upload["with"]["retention-days"] == 30

    privileged = candidate["jobs"]["rc-gate"]
    assert privileged["needs"] == ["mcp-conformance-evidence", "real-llm-evidence"]
    privileged_text = json.dumps(privileged, sort_keys=True)
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in privileged_text
    assert "mcp_conformance_evidence.py verify" in privileged_text
    assert "real_llm_evidence.py verify" in privileged_text
    assert "npm run mcp:conformance" not in privileged_text
    candidate_upload = next(
        step for step in privileged["steps"] if step.get("name") == "Upload release candidate artifacts"
    )
    reviewed_upload = next(
        step
        for step in reviewed["jobs"]["seal-reviewed-evidence"]["steps"]
        if step.get("name") == "Upload immutable reviewed release evidence"
    )
    assert candidate_upload["with"]["retention-days"] == 30
    assert reviewed_upload["with"]["retention-days"] == 30

    assert 'gh run download "$env:LENGRVIS_RELEASE_CANDIDATE_RUN_ID"' in publish_text
    assert "mcp-conformance-evidence-$env:LENGRVIS_RELEASE_CANDIDATE_RUN_ID-" in publish_text
    assert "npm run delivery:rc" in publish_text
    assert "npm run mcp:conformance" not in publish_text


def test_ci_real_llm_secret_is_default_branch_only():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["real-llm-quality"]
    eval_step = next(step for step in job["steps"] if step.get("id") == "real-llm-eval")
    skip_step = next(step for step in job["steps"] if step.get("id") == "real-llm-skip")

    assert "github.event_name != 'pull_request'" in eval_step["if"]
    assert "github.event.repository.default_branch" in eval_step["if"]
    assert eval_step["env"]["LENGRVIS_API_KEY"] == ("${{ secrets.LENGRVIS_REAL_LLM_API_KEY }}")
    assert "secrets." not in json.dumps(skip_step, sort_keys=True)
    for step in job["steps"]:
        if step is not eval_step:
            assert "LENGRVIS_API_KEY" not in step.get("env", {})


def test_release_publish_scopes_github_token_to_cli_steps():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "release-publish.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    expected_cli_steps = {
        "preflight": {
            "Validate immutable release candidate before checkout",
            "Download immutable candidate artifacts into isolated staging",
            "Verify immutable candidate checksums and provenance",
            "Download reviewed release evidence into isolated staging",
        },
        "publish": {"Reverify and publish the fixed candidate asset set"},
    }

    for job_name, expected_steps in expected_cli_steps.items():
        job = workflow["jobs"][job_name]
        job_environment = job.get("env", {})
        assert "GH_TOKEN" not in job_environment
        assert "GITHUB_TOKEN" not in job_environment

        actual_cli_steps: set[str] = set()
        for step in job["steps"]:
            run = step.get("run", "")
            environment = step.get("env", {})
            if re.search(r"\bgh\s+(?:api|run|attestation|release)\b", run):
                actual_cli_steps.add(step["name"])
                assert environment.get("GH_TOKEN") == "${{ github.token }}"
            else:
                assert "GH_TOKEN" not in environment
            assert "GITHUB_TOKEN" not in environment

        assert actual_cli_steps == expected_steps


def test_release_publish_mutation_is_isolated_on_a_clean_runner():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "release-publish.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    preflight = workflow["jobs"]["preflight"]
    publish = workflow["jobs"]["publish"]

    assert preflight["permissions"] == {
        "actions": "read",
        "attestations": "read",
        "contents": "read",
    }
    assert publish["needs"] == "preflight"
    assert publish["environment"] == "production"
    assert publish["permissions"] == {
        "actions": "read",
        "attestations": "read",
        "contents": "write",
    }
    assert len(publish["steps"]) == 1
    clean_step = publish["steps"][0]
    assert "uses" not in clean_step
    assert clean_step["env"]["GH_REPO"] == "${{ github.repository }}"
    clean_run = clean_step["run"]
    for forbidden in (
        "actions/checkout",
        "setup-node",
        "setup-python",
        "npm ",
        "python ",
        "scripts/",
        "release-upload-assets.txt",
        "release-upload-asset-names.txt",
    ):
        assert forbidden not in clean_run
    assert "release-candidate-artifacts-$candidateRunId-$candidateRunAttempt" in clean_run
    assert 'gh run download "$candidateRunId" --repo "$env:GITHUB_REPOSITORY"' in clean_run
    assert "gh attestation verify" in clean_run
    for command in ("view", "delete-asset", "edit", "create", "upload"):
        assert f'gh release {command} "$releaseTag"' in clean_run
    assert clean_run.count('--repo "$env:GITHUB_REPOSITORY"') >= 7


def test_reviewed_evidence_scopes_github_token_to_cli_steps():
    workflow_path = REPO_ROOT / ".github" / "workflows" / "release-reviewed-evidence.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["seal-reviewed-evidence"]

    job_environment = job.get("env", {})
    assert "GH_TOKEN" not in job_environment
    assert "GITHUB_TOKEN" not in job_environment

    expected_cli_steps = {
        "Validate candidate and reviewed-input provenance",
        "Download immutable candidate artifacts",
        "Download exact reviewed-input assets",
    }
    actual_cli_steps: set[str] = set()
    for step in job["steps"]:
        run = step.get("run", "")
        environment = step.get("env", {})
        if re.search(r"\bgh\s+(?:api|run|release)\b", run):
            actual_cli_steps.add(step["name"])
            assert environment.get("GH_TOKEN") == "${{ github.token }}"
        else:
            assert "GH_TOKEN" not in environment
        assert "GITHUB_TOKEN" not in environment

    assert actual_cli_steps == expected_cli_steps


def test_release_artifacts_and_final_assets_are_attempt_and_digest_bound():
    candidate_text = (REPO_ROOT / ".github" / "workflows" / "release-candidate.yml").read_text(encoding="utf-8")
    reviewed_text = (REPO_ROOT / ".github" / "workflows" / "release-reviewed-evidence.yml").read_text(encoding="utf-8")
    publish_text = (REPO_ROOT / ".github" / "workflows" / "release-publish.yml").read_text(encoding="utf-8")

    assert "release-candidate-artifacts-${{ github.run_id }}-${{ github.run_attempt }}" in candidate_text
    assert (
        "release-candidate-artifacts-$env:LENGRVIS_RELEASE_CANDIDATE_RUN_ID-"
        "$env:LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT" in reviewed_text
    )
    assert "release-reviewed-evidence-${{ github.run_id }}-${{ github.run_attempt }}" in reviewed_text
    assert (
        "release-candidate-artifacts-$env:LENGRVIS_RELEASE_CANDIDATE_RUN_ID-"
        "$env:LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT" in publish_text
    )
    assert (
        "release-reviewed-evidence-$env:LENGRVIS_REVIEWED_EVIDENCE_RUN_ID-"
        "$env:LENGRVIS_REVIEWED_EVIDENCE_RUN_ATTEMPT" in publish_text
    )
    assert '"GITHUB_SHA=$commit"' not in publish_text
    assert "asset.digest" in publish_text
    assert "asset.state" in publish_text
    assert "asset.size" in publish_text
    assert "release.target_commitish" in publish_text
    assert publish_text.index("asset.digest") < publish_text.index("--draft=false")


def test_strict_makes_release_evidence_required():
    non_strict_evidence = next(s for s in mod.default_stages(strict=False) if s.name == "evidence")
    strict_evidence = next(s for s in mod.default_stages(strict=True) if s.name == "evidence")
    non_strict_current = next(s for s in mod.default_stages(strict=False) if s.name == "current-release-evidence")
    strict_current = next(s for s in mod.default_stages(strict=True) if s.name == "current-release-evidence")

    assert non_strict_evidence.required is False
    assert strict_evidence.required is True
    assert non_strict_current.required is False
    assert strict_current.required is True
    assert "-StrictReleaseSignoff" in strict_current.command


def test_strict_uses_isolated_mcp_evidence_instead_of_third_party_cli():
    for stages in (
        mod.default_stages(strict=True),
        mod.default_stages(strict=True, candidate_build=True),
        mod.default_stages(strict=True, paid_launch=True),
    ):
        mcp = next(stage for stage in stages if stage.name == "mcp-conformance")
        assert mcp.command == [
            mod.sys.executable,
            "scripts/mcp_conformance_evidence.py",
            "verify",
            "--input",
            ".tmp/qa-evidence/mcp-conformance-job/mcp-conformance-evidence.json",
            "--require-checkout-match",
        ]
        assert mcp.environment_policy == mod.ENVIRONMENT_MCP_EVIDENCE
        assert mcp.timeout_seconds == 30


def test_strict_modes_use_isolated_real_llm_evidence_contract():
    for stages in (
        mod.default_stages(strict=True),
        mod.default_stages(strict=True, candidate_build=True),
        mod.default_stages(strict=True, paid_launch=True),
    ):
        real_llm = next(stage for stage in stages if stage.name == "real-llm-eval")
        assert real_llm.command == [
            mod.sys.executable,
            "scripts/real_llm_evidence.py",
            "verify",
            "--require-checkout-match",
        ]
        assert real_llm.environment_policy == mod.ENVIRONMENT_REAL_LLM_EVIDENCE
        assert real_llm.timeout_seconds == 30


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


def test_candidate_build_requires_automated_and_signed_gates_but_not_reviewed_evidence():
    stages = mod.default_stages(strict=True, candidate_build=True)
    names = [stage.name for stage in stages]

    assert names == [
        "qa-gate",
        "golden-gate",
        "mcp-conformance",
        "maintainability-gate",
        "review-scorecard",
        "agentic-threat-model",
        "real-llm-eval",
        "supply-chain",
        "dependency-audit",
        "secret-scan",
        "security-extensions",
        "release-safety",
        "candidate-binding-context",
        "packaging-verify",
        "signed-artifacts",
    ]
    assert not any(name.endswith("-evidence") for name in names)
    assert "release-owner-signature" not in names


def test_candidate_build_plan_cli_reports_candidate_mode():
    result = _run_plan_cli("--candidate-build")
    payload = json.loads(result.stdout)

    assert payload["candidate_build"] is True
    assert payload["effective_strict"] is True
    assert payload["plan"][-1]["name"] == "signed-artifacts"


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
        "mcp-conformance",
        "maintainability-gate",
        "review-scorecard",
        "agentic-threat-model",
        "real-llm-eval",
        "supply-chain",
        "dependency-audit",
        "secret-scan",
        "security-extensions",
        "release-safety",
        "candidate-binding-context",
        "release-owner-signature",
        "packaging-verify",
        "signed-artifacts",
        "distribution-evidence",
        "clean-machine-evidence",
        "result-quality-evidence",
        "diagnostics-evidence",
        "android-strict-gate",
        "market-readiness",
        "current-release-evidence",
        "readiness",
        "evidence",
    ]
    readiness = next(s for s in mod.default_stages(strict=True) if s.name == "readiness")
    assert "--rc-release" in readiness.command
    assert "--strict" not in readiness.command
    current_evidence = next(s for s in mod.default_stages(strict=True) if s.name == "current-release-evidence")
    assert current_evidence.command == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "./scripts/generate_current_release_evidence.ps1",
        "-StrictReleaseSignoff",
    ]
    market = next(s for s in mod.default_stages(strict=True) if s.name == "market-readiness")
    assert "--strict" in market.command
    assert "--paid-launch" not in market.command
    android = next(s for s in mod.default_stages(strict=True) if s.name == "android-strict-gate")
    assert "LENGRVIS_ANDROID_APK_PATH" in android.command[-1]
    assert "LENGRVIS_ANDROID_REAL_DEVICE_EVIDENCE_PATH" in android.command[-1]
    assert "LENGRVIS_ANDROID_RELEASE_CERTIFICATE_SHA256" in android.command[-1]
    assert "-RequireCandidateBinding" in android.command[-1]
    diagnostics = next(s for s in mod.default_stages(strict=True) if s.name == "diagnostics-evidence")
    for stage_name, verifier in (
        ("distribution-evidence", "verify_distribution_release_evidence.py"),
        ("clean-machine-evidence", "verify_clean_machine_evidence.py"),
        ("result-quality-evidence", "verify_result_quality_reviewed_evidence.py"),
        ("diagnostics-evidence", "verify_diagnostics_external_reviewed_evidence.py"),
    ):
        stage = next(s for s in mod.default_stages(strict=True) if s.name == stage_name)
        assert stage.command == [
            sys.executable,
            f"scripts/{verifier}",
            "--require-candidate-binding",
        ]
    binding_context = next(s for s in mod.default_stages(strict=True) if s.name == "candidate-binding-context")
    assert binding_context.command == [
        sys.executable,
        "scripts/verify_release_candidate_binding.py",
        "--require-checkout-match",
    ]
    assert "actual package content review" in diagnostics.description


def test_paid_launch_adds_commercial_evidence_and_paid_market_gate():
    stages = mod.default_stages(strict=True, paid_launch=True)
    names = [s.name for s in stages]
    assert "commercial-loop" in names
    assert "support-privacy-evidence" in names
    assert "claims-launch-evidence" in names
    assert "commercial-operations-evidence" in names
    assert names.index("commercial-loop") < names.index("support-privacy-evidence")
    assert names.index("support-privacy-evidence") < names.index("claims-launch-evidence")
    assert names.index("claims-launch-evidence") < names.index("commercial-operations-evidence")
    assert names.index("commercial-operations-evidence") < names.index("market-readiness")
    for stage_name in (
        "commercial-loop",
        "support-privacy-evidence",
        "claims-launch-evidence",
        "commercial-operations-evidence",
    ):
        assert "--require-candidate-binding" in next(stage for stage in stages if stage.name == stage_name).command
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
    assert plan and all(
        {
            "name",
            "command",
            "required",
            "description",
            "environment_policy",
            "timeout_seconds",
        }
        <= set(row)
        for row in plan
    )
    mcp = next(row for row in plan if row["name"] == "mcp-conformance")
    assert mcp["environment_policy"] == mod.ENVIRONMENT_MCP_CONFORMANCE


def _run_plan_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), *args, "--plan-only"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )


def test_strict_plan_cli_keeps_no_sale_rc_out_of_commercial_loop():
    result = _run_plan_cli("--strict")
    payload = json.loads(result.stdout)
    names = [stage["name"] for stage in payload["plan"]]
    market = next(stage for stage in payload["plan"] if stage["name"] == "market-readiness")

    assert payload["effective_strict"] is True
    assert "commercial-loop" not in names
    assert "--strict" in market["command"]
    assert "--paid-launch" not in market["command"]
    assert result.stderr == ""


def test_paid_launch_plan_cli_keeps_commercial_evidence_before_market_readiness():
    result = _run_plan_cli("--paid-launch")
    payload = json.loads(result.stdout)
    names = [stage["name"] for stage in payload["plan"]]
    market = next(stage for stage in payload["plan"] if stage["name"] == "market-readiness")

    assert payload["strict"] is False
    assert payload["paid_launch"] is True
    assert payload["effective_strict"] is True
    for name in (
        "commercial-loop",
        "support-privacy-evidence",
        "claims-launch-evidence",
        "commercial-operations-evidence",
    ):
        assert name in names
        assert names.index(name) < names.index("market-readiness")
    assert "--paid-launch" in market["command"]
    assert "--strict" not in market["command"]


def test_strict_plan_cli_reports_ignored_signature_skip_without_polluting_stdout():
    result = _run_plan_cli("--strict", "--skip-signature-verify")
    payload = json.loads(result.stdout)

    assert payload["skip_signature_verify"] is False
    assert any("ignored in strict RC mode" in warning for warning in payload["warnings"])
    assert "ignored in strict RC mode" in result.stderr


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


def test_resolve_stage_command_uses_path_lookup(monkeypatch):
    monkeypatch.setattr(mod.shutil, "which", lambda command: "C:/node/npm.cmd" if command == "npm" else None)

    assert mod.resolve_stage_command(["npm", "run", "qa:gate"]) == [
        "C:/node/npm.cmd",
        "run",
        "qa:gate",
    ]
    assert mod.resolve_stage_command(["definitely-missing"]) == ["definitely-missing"]


def test_mcp_conformance_environment_is_a_strict_runtime_allowlist():
    sensitive = {
        "LENGRVIS_API_KEY": "lengrvis-secret",
        "OPENAI_API_KEY": "provider-secret",
        "GH_TOKEN": "github-cli-secret",
        "GITHUB_TOKEN": "github-actions-secret",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-secret",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc.invalid/token",
        "AZURE_CLIENT_SECRET": "azure-secret",
        "AZURE_TENANT_ID": "tenant-id",
        "LENGRVIS_AZURE_SIGNING_ACCOUNT": "signer",
        "NPM_TOKEN": "npm-secret",
        "NODE_OPTIONS": "--require=./inject.js",
        "PYTHONPATH": "./inject",
        "HTTPS_PROXY": "https://user:secret@proxy.invalid",
        "npm_config_userconfig": "C:/secrets/.npmrc",
    }
    source = {
        "PATH": mod.os.pathsep.join(("node-bin", "python-bin")),
        "SYSTEMROOT": "C:/Windows",
        "TEMP": "C:/Temp",
        "CI": "true",
        **sensitive,
    }
    if mod.os.name != "nt":
        source["Path"] = "poison-bin"
    stage = next(stage for stage in mod.default_stages(strict=False) if stage.name == "mcp-conformance")

    environment = mod.build_stage_environment(stage, source)

    assert environment is not None
    assert environment["PATH"].split(mod.os.pathsep, maxsplit=1) == [
        str(Path(mod.os.path.abspath(mod.sys.executable)).parent),
        source["PATH"],
    ]
    assert environment["SYSTEMROOT"] == source["SYSTEMROOT"]
    assert environment["TEMP"] == source["TEMP"]
    assert environment["CI"] == "true"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONUTF8"] == "1"
    assert environment["npm_config_ignore_scripts"] == "true"
    assert environment["npm_config_userconfig"] == mod.os.devnull
    normalized_keys = {key.upper() for key in environment}
    blocked_keys = {key.upper() for key in sensitive if key.upper() != "NPM_CONFIG_USERCONFIG"}
    assert not normalized_keys.intersection(blocked_keys)
    if mod.os.name != "nt":
        assert "poison-bin" not in environment["PATH"]


def test_run_stage_does_not_pass_release_secrets_to_mcp_conformance(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, env, timeout):
        captured.update(command=command, cwd=cwd, env=env, timeout=timeout)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("LENGRVIS_API_KEY", "lengrvis-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "oidc-secret")
    monkeypatch.setenv("NODE_OPTIONS", "--require=./inject.js")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    stage = next(stage for stage in mod.default_stages(strict=False) if stage.name == "mcp-conformance")

    result = mod.run_stage(stage, cwd=REPO_ROOT)

    assert result.status == "passed"
    environment = captured["env"]
    assert isinstance(environment, dict)
    normalized_keys = {key.upper() for key in environment}
    assert "LENGRVIS_API_KEY" not in normalized_keys
    assert "GITHUB_TOKEN" not in normalized_keys
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in normalized_keys
    assert "NODE_OPTIONS" not in normalized_keys
    assert captured["timeout"] == 300


def test_mcp_evidence_verifier_receives_only_candidate_binding_environment():
    source = {
        "PATH": "runtime-bin",
        "SYSTEMROOT": "C:/Windows",
        "GITHUB_REPOSITORY": "owner/repository",
        "GITHUB_RUN_ID": "999",
        "LENGRVIS_RELEASE_CANDIDATE_COMMIT": "a" * 40,
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ID": "123",
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT": "2",
        "LENGRVIS_API_KEY": "provider-secret",
        "GITHUB_TOKEN": "write-token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-token",
        "NODE_OPTIONS": "--require=./inject.js",
    }
    stage = mod.mcp_conformance_stage(require_isolated_evidence=True)

    environment = mod.build_stage_environment(stage, source)

    assert environment is not None
    assert environment["LENGRVIS_RELEASE_CANDIDATE_COMMIT"] == "a" * 40
    assert environment["LENGRVIS_RELEASE_CANDIDATE_RUN_ID"] == "123"
    assert environment["LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT"] == "2"
    assert environment["GITHUB_REPOSITORY"] == "owner/repository"
    normalized_keys = {key.upper() for key in environment}
    assert "LENGRVIS_API_KEY" not in normalized_keys
    assert "GITHUB_TOKEN" not in normalized_keys
    assert "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in normalized_keys
    assert "NODE_OPTIONS" not in normalized_keys
    assert not any(key.lower().startswith("npm_config_") for key in environment)


def test_real_llm_evidence_verifier_receives_only_candidate_binding_environment():
    source = {
        "PATH": "runtime-bin",
        "SYSTEMROOT": "C:/Windows",
        "GITHUB_REPOSITORY": "owner/repository",
        "GITHUB_RUN_ID": "999",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_SHA": "a" * 40,
        "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY": "owner/repository",
        "LENGRVIS_RELEASE_CANDIDATE_COMMIT": "a" * 40,
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ID": "123",
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT": "2",
        "LENGRVIS_RELEASE_BUILD_IDENTIFIER": "rc-123-2-" + "a" * 40,
        "LENGRVIS_API_KEY": "provider-secret",
        "OPENAI_API_KEY": "provider-secret",
        "GH_TOKEN": "github-cli-secret",
        "GITHUB_TOKEN": "github-actions-secret",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-secret",
        "NODE_OPTIONS": "--require=./inject.js",
        "npm_config_userconfig": "C:/secrets/.npmrc",
    }
    stage = mod.real_llm_evidence_stage()

    environment = mod.build_stage_environment(stage, source)

    assert environment is not None
    for key in (
        "GITHUB_REPOSITORY",
        "GITHUB_RUN_ID",
        "GITHUB_RUN_ATTEMPT",
        "GITHUB_SHA",
        "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY",
        "LENGRVIS_RELEASE_CANDIDATE_COMMIT",
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ID",
        "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT",
        "LENGRVIS_RELEASE_BUILD_IDENTIFIER",
    ):
        assert environment[key] == source[key]
    normalized_keys = {key.upper() for key in environment}
    for forbidden in (
        "LENGRVIS_API_KEY",
        "OPENAI_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
        "NODE_OPTIONS",
    ):
        assert forbidden not in normalized_keys
    assert not any(key.lower().startswith("npm_config_") for key in environment)


def test_run_stage_rejects_unknown_environment_policy(monkeypatch):
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unknown policy launched a child process")),
    )
    stage = mod.Stage(
        "invalid-policy",
        ["python", "-V"],
        environment_policy="unexpected-policy",
    )

    result = mod.run_stage(stage, cwd=REPO_ROOT)

    assert result.status == "failed"
    assert result.exit_code is None
    assert "unsupported stage environment policy" in result.detail


def test_run_stage_returns_structured_failure_for_timeout(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["npm", "run", "mcp:conformance"], 300)

    monkeypatch.setattr(mod.subprocess, "run", timeout)
    stage = mod.mcp_conformance_stage(require_isolated_evidence=False)

    result = mod.run_stage(stage, cwd=REPO_ROOT)

    assert result.status == "failed"
    assert result.exit_code is None
    assert result.detail == "stage timed out after 300 seconds"


def test_run_stage_returns_structured_failure_for_os_error(monkeypatch):
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("bad executable")),
    )

    result = mod.run_stage(mod.Stage("broken", ["broken"]), cwd=REPO_ROOT)

    assert result.status == "failed"
    assert result.exit_code is None
    assert result.detail == "stage launch failed: bad executable"


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
