#!/usr/bin/env python3
"""Validate the full review scorecard.

The scorecard is review evidence, not release sign-off. This checker keeps the
score internally consistent and prevents a 100/100 claim while release P0 rows
remain unfinished.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCORECARD = ROOT / "docs" / "qa" / "full-review-scorecard.md"
DEFAULT_READINESS = ROOT / "docs" / "release" / "release-readiness-dashboard.md"
RELEASE_READINESS_CHECKER = ROOT / "scripts" / "check_release_readiness_dashboard.py"
REQUIRED_WIRING = (
    (ROOT / "package.json", '"review:scorecard"'),
    (ROOT / ".github" / "workflows" / "ci.yml", "npm run review:scorecard"),
    (
        ROOT / ".github" / "workflows" / "release-candidate.yml",
        "npm run review:scorecard",
    ),
    (
        ROOT / ".github" / "workflows" / "release-readiness.yml",
        "npm run review:scorecard",
    ),
    (
        ROOT / ".github" / "workflows" / "release-publish.yml",
        "npm run review:scorecard",
    ),
    (
        ROOT / "docs" / "release" / "delivery-pipeline.md",
        "`npm run review:scorecard` verifies the full-review scorecard before any",
    ),
    (
        ROOT / "docs" / "release" / "release-readiness-dashboard.md",
        "npm run review:scorecard",
    ),
    (
        ROOT / "docs" / "qa" / "release-gate.md",
        "candidate-bound MCP and real-LLM quality evidence, maintainability gate, `review:scorecard`",
    ),
    (ROOT / "scripts" / "delivery_pipeline.py", '"review-scorecard"'),
    (
        ROOT / "scripts" / "generate_current_release_evidence.ps1",
        "npm run review:scorecard",
    ),
)

SCORE_RE = re.compile(
    r"^\| (?P<area>[^|]+) \| (?P<score>\d+)\s*/\s*(?P<max>\d+) \|", re.MULTILINE
)
TOTAL_RE = re.compile(r"^Total:\s*(?P<score>\d+)\s*/\s*(?P<max>\d+)\.", re.MULTILINE)
RR_P0_RE = re.compile(
    r"^\| (?P<id>RR-P0-\d+) \| [^|]+ \| [^|]+ \| (?P<status>[^|]+) \|", re.MULTILINE
)
NUMBERED_STEP_RE = re.compile(r"^\d+\.\s+", re.MULTILINE)


def _load_release_readiness_checker():
    spec = importlib.util.spec_from_file_location(
        "check_release_readiness_dashboard", RELEASE_READINESS_CHECKER
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {RELEASE_READINESS_CHECKER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_score_summary(scorecard_text: str) -> dict[str, object]:
    rows = [
        {
            "area": match.group("area").strip(),
            "score": int(match.group("score")),
            "max": int(match.group("max")),
        }
        for match in SCORE_RE.finditer(scorecard_text)
    ]
    total_match = TOTAL_RE.search(scorecard_text)
    return {
        "total": int(total_match.group("score")) if total_match else None,
        "max": int(total_match.group("max")) if total_match else None,
        "rows": rows,
    }


def parse_readiness_summary(readiness_text: str) -> dict[str, object]:
    release_checker = _load_release_readiness_checker()
    p0_rows = [
        row
        for row in release_checker.parse_rows(readiness_text)
        if row.row_id.startswith("RR-P0-")
    ]
    return {
        "p0_total": len(p0_rows),
        "p0_passed": sum(1 for row in p0_rows if row.status == "passed"),
        "p0_waived": sum(1 for row in p0_rows if row.status == "waived"),
        "p0_blocked": sum(1 for row in p0_rows if row.status == "blocked"),
        "p0_in_progress": sum(1 for row in p0_rows if row.status == "in_progress"),
        "p0_rows": [
            {
                "id": row.row_id,
                "area": row.area,
                "status": row.status,
                "artifact": row.artifact,
                "owner": row.owner,
            }
            for row in p0_rows
        ],
    }


def validate_scorecard(
    scorecard_text: str,
    readiness_text: str,
    *,
    artifact_root: Path | None = None,
    current_sha: str | None = None,
    expected_repo: str | None = None,
    expected_run_id: str | None = None,
) -> list[str]:
    errors: list[str] = []
    artifact_root = artifact_root or ROOT
    score_summary = parse_score_summary(scorecard_text)
    scores = [(int(row["score"]), int(row["max"])) for row in score_summary["rows"]]
    if not scores:
        errors.append("score table must contain at least one '<score> / <max>' row")

    total_match = TOTAL_RE.search(scorecard_text)
    if total_match is None:
        errors.append("scorecard must include a 'Total: N / 100.' line")
        total_score = None
        total_max = None
    else:
        total_score = int(total_match.group("score"))
        total_max = int(total_match.group("max"))
        if total_max != 100:
            errors.append("total maximum must be 100")

    score_sum = sum(score for score, _max in scores)
    max_sum = sum(_max for _score, _max in scores)
    if total_match is not None and total_score != score_sum:
        errors.append(f"total score {total_score} does not match row sum {score_sum}")
    if total_match is not None and total_max != max_sum:
        errors.append(f"total max {total_max} does not match row max sum {max_sum}")

    if "review evidence, not release sign-off" not in scorecard_text:
        errors.append("scorecard must explicitly say it is not release sign-off")
    if "Do not mark this scorecard 100/100" not in scorecard_text:
        errors.append("scorecard must keep a fail-closed 100/100 warning")

    rr_p0_statuses = {
        match.group("id"): match.group("status").strip()
        for match in RR_P0_RE.finditer(readiness_text)
    }
    release_checker = _load_release_readiness_checker()
    missing_p0_rows = sorted(
        release_checker.REQUIRED_PUBLIC_BETA_P0_IDS - set(rr_p0_statuses)
    )
    if missing_p0_rows:
        errors.append(
            "release readiness dashboard is missing required public Beta P0 rows: "
            + ", ".join(missing_p0_rows)
        )
    unfinished = {
        key: value for key, value in rr_p0_statuses.items() if value != "passed"
    }
    if total_score == 100 and unfinished:
        blocked = ", ".join(
            f"{key}={value}" for key, value in sorted(unfinished.items())
        )
        errors.append(
            f"scorecard cannot claim 100/100 while release P0 rows are unfinished: {blocked}"
        )
    if total_score == 100 and not unfinished:
        release_errors, _release_warnings = release_checker.validate(
            release_checker.parse_rows(readiness_text),
            strict=True,
            rc_release=True,
            artifact_root=artifact_root,
            dashboard_text=readiness_text,
            current_sha=current_sha,
            expected_repo=expected_repo,
            expected_run_id=expected_run_id,
        )
        if release_errors:
            errors.append(
                "scorecard cannot claim 100/100 until strict RC release readiness passes: "
                + "; ".join(release_errors)
            )

    required_phrases = (
        "npm run release:readiness:rc",
        "npm run security:threat-model",
        "npm run evidence:diagnostics-verify",
        "diagnostics-external-review-evidence-reviewed.json",
        "machine_gates_passed",
        "release-owner approval/signature",
    )
    for phrase in required_phrases:
        if phrase not in scorecard_text:
            errors.append(f"scorecard is missing required phrase: {phrase}")

    if len(NUMBERED_STEP_RE.findall(scorecard_text)) < 8:
        errors.append("Path To 100 must include at least eight numbered steps")
    return errors


def validate_repo_wiring(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for path, phrase in REQUIRED_WIRING:
        candidate = root / path.relative_to(ROOT)
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"could not read scorecard wiring file {candidate}: {exc}")
            continue
        if phrase not in text:
            errors.append(f"{candidate} must include {phrase!r}")
    return errors


def validate_worktree(root: Path = ROOT) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        return [f"full-review scorecard requires git worktree verification: {exc}"]
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        suffix = f": {detail}" if detail else ""
        return [f"full-review scorecard could not inspect the worktree{suffix}"]
    changes = [line for line in result.stdout.splitlines() if line.strip()]
    if not changes:
        return []
    preview = "; ".join(changes[:5])
    if len(changes) > 5:
        preview += "; ..."
    return [
        f"full-review scorecard requires a clean worktree; found {len(changes)} change(s): {preview}"
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--readiness", type=Path, default=DEFAULT_READINESS)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Skip the clean-worktree guard for isolated scorecard document validation only.",
    )
    args = parser.parse_args()

    errors: list[str] = []
    try:
        scorecard_text = args.scorecard.read_text(encoding="utf-8")
    except OSError as exc:
        scorecard_text = ""
        errors.append(f"could not read scorecard: {exc}")
    try:
        readiness_text = args.readiness.read_text(encoding="utf-8")
    except OSError as exc:
        readiness_text = ""
        errors.append(f"could not read readiness dashboard: {exc}")
    if not errors:
        errors.extend(
            validate_scorecard(scorecard_text, readiness_text, artifact_root=ROOT)
        )
        errors.extend(validate_repo_wiring(ROOT))
        if not args.allow_dirty:
            errors.extend(validate_worktree(ROOT))

    payload = {
        "ok": not errors,
        "scorecard": str(args.scorecard),
        "readiness": str(args.readiness),
        "score": parse_score_summary(scorecard_text),
        "release_readiness": parse_readiness_summary(readiness_text),
        "errors": errors,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
