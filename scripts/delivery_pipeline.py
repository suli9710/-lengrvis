#!/usr/bin/env python3
"""Fail-closed delivery pipeline orchestrator for Lengrvis.

Runs the real delivery chain in order and emits a single machine-readable
verdict that other tooling and the release owner can trust:

    qa-gate -> golden-gate -> mcp-conformance -> maintainability-gate
             -> review-scorecard -> supply-chain -> security-extensions -> release-safety
             -> owner-signature -> market-readiness -> current-release-evidence
             -> readiness -> evidence

Design notes:
- Pure helpers (default_stages, build_plan, aggregate_verdict) carry the policy
  and are unit-tested without invoking the toolchain.
- Command execution is real but guarded: use --plan-only on machines that cannot
  run the full Windows/desktop/mobile toolchain.
- The pipeline is fail-closed: any failed required stage blocks the verdict and
  returns a non-zero exit code. Remaining stages are skipped unless --keep-going.
- A release candidate must run with --strict so the readiness dashboard enforces
  that every P0 stop-ship blocker is passed. Scoped maintenance waivers are not
  release-candidate sign-off.
- Non-strict delivery:run still runs signed-artifacts unless
  --skip-signature-verify is passed (explicit dev opt-out; emits warnings).
"""

from __future__ import annotations

import argparse
import json
import shutil
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

DASHBOARD = "docs/release/release-readiness-dashboard.md"
MARKET_DASHBOARD = "docs/business/market-readiness.md"


@dataclass(frozen=True)
class Stage:
    name: str
    command: List[str]
    required: bool = True
    description: str = ""


@dataclass
class StageResult:
    name: str
    required: bool
    status: str  # "passed" | "failed" | "skipped"
    exit_code: Optional[int] = None
    duration_s: Optional[float] = None
    detail: str = ""


def signed_artifacts_stage() -> Stage:
    return Stage(
        "signed-artifacts",
        [
            "npm",
            "--prefix",
            "desktop",
            "run",
            "verify:windows-release-signatures",
        ],
        True,
        "Verify Windows release artifact signatures",
    )


def release_artifact_preflight_stage() -> Stage:
    return Stage(
        "release-artifact-preflight",
        [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; "
                "missing = [str(p) for p in (Path('dist/backend.exe'),) if not p.is_file()]; "
                "sys.exit(1 if missing else 0)"
            ),
        ],
        True,
        "Ensure dist/backend.exe exists before signature verification",
    )


def current_release_evidence_stage(*, strict: bool) -> Stage:
    command = ["npm", "run", "evidence:current-release"]
    if strict:
        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "./scripts/generate_current_release_evidence.ps1",
            "-StrictReleaseSignoff",
        ]
    return Stage(
        "current-release-evidence",
        command,
        False,
        "Generate current release evidence for this candidate",
    )


def release_owner_signature_stage() -> Stage:
    return Stage(
        "release-owner-signature",
        [
            sys.executable,
            "scripts/release_owner_signature.py",
            "--output",
            "build/release-owner-signature-verification.json",
        ],
        True,
        "Verify detached Ed25519 release-owner approval for the exact candidate",
    )


def default_stages(
    *,
    strict: bool,
    skip_signature_verify: bool = False,
    paid_launch: bool = False,
    candidate_build: bool = False,
) -> List[Stage]:
    """Return the ordered delivery stages.

    The readiness stage gains --strict for release candidates so blocked P0
    rows fail the pipeline instead of only warning.

    Non-strict runs still verify Windows release signatures unless
    ``skip_signature_verify`` is set (explicit dev opt-out only).
    """
    effective_strict = strict or paid_launch or candidate_build
    readiness_cmd = [
        sys.executable,
        "scripts/check_release_readiness_dashboard.py",
        "--dashboard",
        DASHBOARD,
    ]
    market_readiness_cmd = [
        sys.executable,
        "scripts/check_market_readiness.py",
        "--dashboard",
        MARKET_DASHBOARD,
    ]
    if paid_launch:
        readiness_cmd.append("--rc-release")
        market_readiness_cmd.append("--paid-launch")
    elif effective_strict:
        readiness_cmd.append("--rc-release")
        market_readiness_cmd.append("--strict")
    stages = [
        Stage(
            "qa-gate",
            ["npm", "run", "qa:gate"],
            True,
            "Tests, typecheck, desktop smoke",
        ),
        Stage(
            "golden-gate",
            ["npm", "run", "golden:gate"],
            True,
            "Mock-provider deterministic golden tasks",
        ),
        Stage(
            "mcp-conformance",
            ["npm", "run", "mcp:conformance"],
            True,
            "Official MCP lifecycle, tools, and SSE resume conformance",
        ),
        Stage(
            "maintainability-gate",
            ["npm", "run", "maintainability:gate"],
            True,
            "Source-size anti-regrowth gate",
        ),
        Stage(
            "review-scorecard",
            ["npm", "run", "review:scorecard"],
            True,
            "Full-review scorecard consistency gate",
        ),
        Stage(
            "agentic-threat-model",
            ["npm", "run", "security:threat-model"],
            True,
            "Versioned trust-boundary and OWASP Agentic control-map gate",
        ),
        Stage(
            "supply-chain",
            ["npm", "run", "supply-chain:verify"],
            True,
            "Dependency locks + SBOM",
        ),
        Stage(
            "dependency-audit",
            ["npm", "run", "audit:deps"],
            True,
            "npm audit + pip-audit vulnerability gate",
        ),
        Stage(
            "secret-scan",
            ["npm", "run", "security:secrets"],
            True,
            "Strict gitleaks source scan",
        ),
        Stage(
            "security-extensions",
            ["npm", "run", "security:extensions"],
            True,
            "Extension/skill security gate",
        ),
        Stage(
            "release-safety",
            ["npm", "run", "release:safety"],
            True,
            "Release configuration and execution-isolation fail-closed checks",
        ),
        Stage(
            "market-readiness",
            market_readiness_cmd,
            True,
            "Commercial launch dashboard validation",
        ),
        current_release_evidence_stage(strict=effective_strict),
        Stage(
            "readiness", readiness_cmd, True, "Release readiness dashboard validation"
        ),
        Stage(
            "evidence",
            ["npm", "run", "evidence:release"],
            False,
            "Collect release evidence packet",
        ),
    ]
    if not effective_strict and not skip_signature_verify:
        insert_at = next(i for i, stage in enumerate(stages) if stage.name == "market-readiness")
        stages.insert(insert_at, release_artifact_preflight_stage())
        stages.insert(insert_at + 1, signed_artifacts_stage())
    if effective_strict:
        by_name = {stage.name: stage for stage in stages}
        evidence_stage = by_name["evidence"]
        by_name["evidence"] = Stage(
            evidence_stage.name,
            evidence_stage.command,
            True,
            evidence_stage.description,
        )
        current_evidence_stage = by_name["current-release-evidence"]
        by_name["current-release-evidence"] = Stage(
            current_evidence_stage.name,
            current_evidence_stage.command,
            True,
            current_evidence_stage.description,
        )
        stages = [
            by_name["qa-gate"],
            by_name["golden-gate"],
            by_name["mcp-conformance"],
            by_name["maintainability-gate"],
            by_name["review-scorecard"],
            by_name["agentic-threat-model"],
            Stage(
                "real-llm-eval",
                [sys.executable, "scripts/run_real_llm_eval.py", "--quality-gate"],
                True,
                "Real provider golden replay quality gate",
            ),
            by_name["supply-chain"],
            by_name["dependency-audit"],
            by_name["secret-scan"],
            by_name["security-extensions"],
            by_name["release-safety"],
            Stage(
                "candidate-binding-context",
                [
                    sys.executable,
                    "scripts/verify_release_candidate_binding.py",
                    "--require-checkout-match",
                ],
                True,
                "Verify the explicit immutable candidate identity matches this checkout",
            ),
            *([] if candidate_build else [release_owner_signature_stage()]),
            Stage(
                "packaging-verify",
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    "./scripts/build_all.ps1",
                    "-VerifyOnly",
                    "-RunExecutableSmoke",
                    "-SmokeTimeoutSeconds",
                    "45",
                ],
                True,
                "Verify release artifacts and run executable smoke",
            ),
            signed_artifacts_stage(),
            Stage(
                "distribution-evidence",
                [
                    sys.executable,
                    "scripts/verify_distribution_release_evidence.py",
                    "--require-candidate-binding",
                ],
                True,
                "Reviewed signing/distribution evidence",
            ),
            Stage(
                "clean-machine-evidence",
                [
                    sys.executable,
                    "scripts/verify_clean_machine_evidence.py",
                    "--require-candidate-binding",
                ],
                True,
                "Reviewed clean-machine install/runtime evidence",
            ),
            Stage(
                "result-quality-evidence",
                [
                    sys.executable,
                    "scripts/verify_result_quality_reviewed_evidence.py",
                    "--require-candidate-binding",
                ],
                True,
                "Reviewed 30+ task natural-language result quality evidence",
            ),
            Stage(
                "diagnostics-evidence",
                [
                    sys.executable,
                    "scripts/verify_diagnostics_external_reviewed_evidence.py",
                    "--require-candidate-binding",
                ],
                True,
                "Verify signed diagnostics reviewed evidence; blocks until actual package content review is recorded",
            ),
            Stage(
                "android-strict-gate",
                [
                    "powershell",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    (
                        "& ./scripts/verify_android_release_gate.ps1 "
                        "-ArtifactPath $env:LENGRVIS_ANDROID_APK_PATH "
                        "-RealDeviceEvidencePath $env:LENGRVIS_ANDROID_REAL_DEVICE_EVIDENCE_PATH "
                        "-ExpectedSignerCertificateSha256 $env:LENGRVIS_ANDROID_RELEASE_CERTIFICATE_SHA256 "
                        "-RequireCandidateBinding"
                    ),
                ],
                True,
                "Strict Android SDK signature/package/version, controlled signer, provenance, and reviewed LAN/WSS evidence gate",
            ),
            *(
                [
                    Stage(
                        "commercial-loop",
                        [
                            sys.executable,
                            "scripts/verify_commercial_loop_evidence.py",
                            "--require-candidate-binding",
                        ],
                        True,
                        "Reviewed Free/Plus/Pro subscription activation commercial loop evidence",
                    ),
                    Stage(
                        "support-privacy-evidence",
                        [
                            sys.executable,
                            "scripts/verify_support_privacy_rehearsal_evidence.py",
                            "--require-candidate-binding",
                        ],
                        True,
                        "Reviewed support and privacy operations rehearsal evidence",
                    ),
                    Stage(
                        "claims-launch-evidence",
                        [
                            sys.executable,
                            "scripts/verify_launch_claims_reviewed_evidence.py",
                            "--require-candidate-binding",
                        ],
                        True,
                        "Reviewed paid-launch claims and asset evidence",
                    ),
                    Stage(
                        "commercial-operations-evidence",
                        [
                            sys.executable,
                            "scripts/verify_commercial_operations_evidence.py",
                            "--require-candidate-binding",
                        ],
                        True,
                        "Reviewed legal, payment, tax, support, refunds, and claims operations evidence",
                    ),
                ]
                if paid_launch
                else []
            ),
            by_name["market-readiness"],
            by_name["current-release-evidence"],
            by_name["readiness"],
            by_name["evidence"],
        ]
        if candidate_build:
            # A candidate must be buildable, testable and signed before it can
            # be handed to reviewers. Human-reviewed evidence is necessarily a
            # later promotion input and must not make candidate creation
            # circular.
            stages = stages[: stages.index(next(stage for stage in stages if stage.name == "signed-artifacts")) + 1]
    return stages


def build_plan(stages: List[Stage]) -> List[dict]:
    return [
        {
            "name": stage.name,
            "command": " ".join(shlex.quote(part) for part in stage.command),
            "required": stage.required,
            "description": stage.description,
        }
        for stage in stages
    ]


def aggregate_verdict(results: List[StageResult]) -> dict:
    required_failures = [r.name for r in results if r.required and r.status == "failed"]
    optional_failures = [
        r.name for r in results if not r.required and r.status == "failed"
    ]
    skipped = [r.name for r in results if r.status == "skipped"]
    ok = not required_failures
    return {
        "ok": ok,
        "decision": "passed" if ok else "blocked",
        "required_failures": required_failures,
        "optional_failures": optional_failures,
        "skipped": skipped,
        "stages": [asdict(r) for r in results],
    }


def resolve_stage_command(command: List[str]) -> List[str]:
    """Resolve PATHEXT-backed commands such as npm on Windows.

    Python's CreateProcess path does not consistently apply PATHEXT the way an
    interactive shell does, so ``["npm", ...]`` can fail even when ``where npm``
    shows ``npm.cmd``. Keep the stage definitions shell-neutral and resolve the
    executable at the boundary.
    """
    if not command:
        return command
    executable = shutil.which(command[0])
    if executable:
        return [executable, *command[1:]]
    return command


def run_stage(stage: Stage, *, cwd: Path) -> StageResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(resolve_stage_command(stage.command), cwd=str(cwd))
        code = proc.returncode
    except FileNotFoundError as exc:
        return StageResult(
            stage.name,
            stage.required,
            "failed",
            None,
            round(time.monotonic() - start, 3),
            f"command not found: {exc}",
        )
    status = "passed" if code == 0 else "failed"
    return StageResult(
        stage.name, stage.required, status, code, round(time.monotonic() - start, 3)
    )


def run_pipeline(
    stages: List[Stage], *, cwd: Path, keep_going: bool
) -> List[StageResult]:
    results: List[StageResult] = []
    halted = False
    for index, stage in enumerate(stages):
        if halted:
            results.append(StageResult(stage.name, stage.required, "skipped"))
            continue
        result = run_stage(stage, cwd=cwd)
        results.append(result)
        if result.status == "failed" and result.required and not keep_going:
            halted = True
    return results


def build_signature_verify_warnings(
    *,
    strict: bool,
    skip_signature_verify_requested: bool,
) -> tuple[bool, list[str]]:
    """Return effective skip flag and warning lines for the delivery verdict."""
    effective_skip = skip_signature_verify_requested and not strict
    warnings: list[str] = []
    if skip_signature_verify_requested and strict:
        warnings.append("--skip-signature-verify is ignored in strict RC mode")
    if effective_skip:
        warnings.append(
            "signed-artifacts skipped via --skip-signature-verify; "
            "non-strict delivery:run does not verify release signatures"
        )
    return effective_skip, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Lengrvis delivery pipeline.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Enforce strict release readiness (RC mode).",
    )
    parser.add_argument(
        "--paid-launch",
        action="store_true",
        help=(
            "Enforce paid/public launch readiness. This implies strict RC release "
            "readiness and requires passed MR-P0 commercial evidence."
        ),
    )
    parser.add_argument(
        "--candidate-build",
        action="store_true",
        help="Build and verify an immutable signed candidate without requiring later reviewed evidence.",
    )
    parser.add_argument(
        "--skip-signature-verify",
        action="store_true",
        help=(
            "Skip the signed-artifacts stage in non-strict runs (dev opt-out only; "
            "ignored with --strict)."
        ),
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print the ordered plan without executing.",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Run remaining stages after a required failure.",
    )
    parser.add_argument(
        "--repo-root", default=".", help="Repository root used as working directory."
    )
    parser.add_argument(
        "--output", default="", help="Optional path to write the JSON verdict."
    )
    args = parser.parse_args()
    if args.candidate_build and args.paid_launch:
        parser.error("--candidate-build and --paid-launch are mutually exclusive")

    skip_signature_verify_requested = bool(args.skip_signature_verify)
    effective_strict = bool(args.strict or args.paid_launch or args.candidate_build)
    skip_signature_verify, signature_verify_warnings = build_signature_verify_warnings(
        strict=effective_strict,
        skip_signature_verify_requested=skip_signature_verify_requested,
    )
    for line in signature_verify_warnings:
        print(f"warning: {line}", file=sys.stderr)
    stages = default_stages(
        strict=effective_strict,
        skip_signature_verify=skip_signature_verify,
        paid_launch=bool(args.paid_launch),
        candidate_build=bool(args.candidate_build),
    )

    if args.plan_only:
        print(
            json.dumps(
                {
                    "strict": args.strict,
                    "paid_launch": args.paid_launch,
                    "candidate_build": args.candidate_build,
                    "effective_strict": effective_strict,
                    "skip_signature_verify": skip_signature_verify,
                    "warnings": signature_verify_warnings,
                    "plan": build_plan(stages),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    repo_root = Path(args.repo_root).resolve()
    results = run_pipeline(stages, cwd=repo_root, keep_going=args.keep_going)
    verdict = aggregate_verdict(results)
    payload = {
        "strict": args.strict,
        "paid_launch": args.paid_launch,
        "candidate_build": args.candidate_build,
        "effective_strict": effective_strict,
        "skip_signature_verify": skip_signature_verify,
        "warnings": signature_verify_warnings,
        **verdict,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
