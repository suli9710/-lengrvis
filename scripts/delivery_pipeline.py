#!/usr/bin/env python3
"""Fail-closed delivery pipeline orchestrator for Lengrvis.

Runs the real delivery chain in order and emits a single machine-readable
verdict that other tooling and the release owner can trust:

    qa-gate -> supply-chain -> security-extensions -> release-safety
             -> market-readiness -> readiness -> evidence

Design notes:
- Pure helpers (default_stages, build_plan, aggregate_verdict) carry the policy
  and are unit-tested without invoking the toolchain.
- Command execution is real but guarded: use --plan-only on machines that cannot
  run the full Windows/desktop/mobile toolchain.
- The pipeline is fail-closed: any failed required stage blocks the verdict and
  returns a non-zero exit code. Remaining stages are skipped unless --keep-going.
- A release candidate must run with --strict so the readiness dashboard enforces
  that every P0 stop-ship blocker is passed or explicitly waived.
"""

from __future__ import annotations

import argparse
import json
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


def default_stages(*, strict: bool) -> List[Stage]:
    """Return the ordered delivery stages.

    The readiness stage gains --strict for release candidates so blocked P0
    rows fail the pipeline instead of only warning.
    """
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
    if strict:
        readiness_cmd.append("--strict")
        market_readiness_cmd.append("--strict")
    return [
        Stage("qa-gate", ["npm", "run", "qa:gate"], True, "Tests, typecheck, desktop smoke"),
        Stage("supply-chain", ["npm", "run", "supply-chain:verify"], True, "Dependency locks + SBOM"),
        Stage("security-extensions", ["npm", "run", "security:extensions"], True, "Extension/skill security gate"),
        Stage("release-safety", ["npm", "run", "release:safety"], True, "Release safety checks"),
        Stage("market-readiness", market_readiness_cmd, True, "Commercial launch dashboard validation"),
        Stage("readiness", readiness_cmd, True, "Release readiness dashboard validation"),
        Stage("evidence", ["npm", "run", "evidence:release"], False, "Collect release evidence packet"),
    ]


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
    optional_failures = [r.name for r in results if not r.required and r.status == "failed"]
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


def run_stage(stage: Stage, *, cwd: Path) -> StageResult:
    start = time.monotonic()
    try:
        proc = subprocess.run(stage.command, cwd=str(cwd))
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
    return StageResult(stage.name, stage.required, status, code, round(time.monotonic() - start, 3))


def run_pipeline(stages: List[Stage], *, cwd: Path, keep_going: bool) -> List[StageResult]:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Lengrvis delivery pipeline.")
    parser.add_argument("--strict", action="store_true", help="Enforce strict release readiness (RC mode).")
    parser.add_argument("--plan-only", action="store_true", help="Print the ordered plan without executing.")
    parser.add_argument("--keep-going", action="store_true", help="Run remaining stages after a required failure.")
    parser.add_argument("--repo-root", default=".", help="Repository root used as working directory.")
    parser.add_argument("--output", default="", help="Optional path to write the JSON verdict.")
    args = parser.parse_args()

    stages = default_stages(strict=args.strict)

    if args.plan_only:
        print(json.dumps({"strict": args.strict, "plan": build_plan(stages)}, ensure_ascii=False, indent=2))
        return 0

    repo_root = Path(args.repo_root).resolve()
    results = run_pipeline(stages, cwd=repo_root, keep_going=args.keep_going)
    verdict = aggregate_verdict(results)
    payload = {"strict": args.strict, **verdict}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
