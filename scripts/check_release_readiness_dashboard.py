#!/usr/bin/env python3
"""Validate the release readiness dashboard.

Default mode prints a machine-readable summary and exits 0 unless the dashboard is
missing or malformed. Strict mode fails when any P0 blocker is not passed or
explicitly waived. RC release mode is stricter: every P0 row must be passed, so a
scope-limited maintenance waiver cannot be mistaken for release-candidate
sign-off.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_STATUSES = {"blocked", "in_progress", "passed", "waived"}
STRICT_ALLOWED_P0_STATUSES = {"passed", "waived"}
STRICT_ACCEPTED_MANUAL_SIGNOFF_STATUSES = {
    "rc_signoff_recorded",
    "release_signoff_recorded",
    "paid_launch_signoff_recorded",
}
P0_PREFIX = "RR-P0-"
ROW_RE = re.compile(r"^\|\s*(RR-[^|]+?)\s*\|(?P<body>.*)\|\s*$")
CI_ARTIFACT_PATH_PREFIXES = (
    ".tmp/qa-evidence/",
    ".tmp/release-evidence-packet/",
    ".tmp/packaging-smoke/",
    "build/",
    "desktop/release/",
)
ISSUE_KEY_RE = r"(?:#[0-9]+|[A-Z][A-Z0-9]+-[0-9]+)"
ISSUE_URL_RE = re.compile(
    r"https?://[^\s)]+(?:"
    r"/issues/[0-9]+|"
    r"/pull/[0-9]+|"
    r"/-/issues/[0-9]+|"
    r"/-/merge_requests/[0-9]+|"
    r"/browse/[A-Z][A-Z0-9]+-[0-9]+|"
    r"/issue/[A-Z][A-Z0-9]+-[0-9]+"
    r")(?:[/?#][^\s)]*)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReadinessRow:
    row_id: str
    area: str
    status: str
    artifact: str
    owner: str
    expiry: str
    notes: str


def _split_markdown_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_rows(markdown: str) -> list[ReadinessRow]:
    rows: list[ReadinessRow] = []
    for line in markdown.splitlines():
        match = ROW_RE.match(line)
        if not match:
            continue
        cells = _split_markdown_row(line)
        if len(cells) < 7:
            continue
        row_id = cells[0]
        if row_id == "ID" or set(row_id) <= {"-"}:
            continue
        # Stop-ship rows have: ID, Area, Required evidence, Status, Artifact, Owner, Expiry, Notes.
        # P1 rows have: ID, Area, Required change, Status, Artifact, Owner, Notes.
        rows.append(
            ReadinessRow(
                row_id=row_id,
                area=cells[1],
                status=cells[3].strip("`").lower(),
                artifact=cells[4] if len(cells) > 4 else "",
                owner=cells[5] if len(cells) > 5 else "",
                expiry=cells[6] if len(cells) > 7 else "",
                notes=cells[-1] if cells else "",
            )
        )
    return rows


def validate(
    rows: list[ReadinessRow],
    *,
    strict: bool,
    rc_release: bool = False,
    artifact_root: Path | None = None,
    dashboard_text: str = "",
    expected_repo: str | None = None,
    current_sha: str | None = None,
    expected_run_id: str | None = None,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    artifact_root = artifact_root or Path.cwd()
    git_repo = _git_remote_github_repo(artifact_root)
    github_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    expected_repo = (expected_repo or github_repo or git_repo).strip()
    expected_run_id = (expected_run_id or os.environ.get("GITHUB_RUN_ID", "")).strip()
    git_sha = _git_head_sha(artifact_root)
    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    current_sha = (current_sha or git_sha or github_sha).strip() if strict else ""
    if not rows:
        errors.append("No readiness rows found.")
        return errors, warnings

    if strict:
        if github_repo and git_repo and github_repo.lower() != git_repo.lower():
            errors.append(
                f"GITHUB_REPOSITORY {github_repo} does not match checked-out repository {git_repo}."
            )
        if github_sha and git_sha and not _sha_matches(github_sha, git_sha):
            errors.append(
                f"GITHUB_SHA {github_sha[:8]} does not match checked-out HEAD {git_sha[:8]}."
            )
        if not expected_repo:
            errors.append("Strict release readiness requires a current GitHub repository binding.")
        if not current_sha:
            errors.append("Strict release readiness requires a current commit binding.")

    p0_rows = [row for row in rows if row.row_id.startswith(P0_PREFIX)]
    if not p0_rows:
        errors.append("No P0 stop-ship rows found.")

    for row in rows:
        if row.status not in ALLOWED_STATUSES:
            errors.append(f"{row.row_id}: invalid status '{row.status}'.")
        if row.status in {"passed", "waived"}:
            if not row.owner or row.owner.upper() == "TBD":
                errors.append(f"{row.row_id}: {row.status} row requires an owner.")
            if not row.artifact or row.artifact.upper() == "TBD":
                errors.append(
                    f"{row.row_id}: {row.status} row requires an artifact/link label."
                )
        if row.row_id.startswith(P0_PREFIX) and row.status != "passed":
            warnings.append(f"{row.row_id}: stop-ship row is {row.status}.")
        if (
            strict
            and row.row_id.startswith(P0_PREFIX)
            and row.status not in STRICT_ALLOWED_P0_STATUSES
        ):
            errors.append(
                f"{row.row_id}: strict release readiness requires passed or waived, got {row.status}."
            )
        if rc_release and row.row_id.startswith(P0_PREFIX) and row.status != "passed":
            errors.append(
                f"{row.row_id}: RC release requires passed P0 evidence; "
                f"{row.status} is only allowed for scoped maintenance packaging."
            )
        if (
            strict
            and row.status in {"passed", "waived"}
            and not _artifact_is_verifiable(row.artifact, artifact_root)
        ):
            errors.append(
                f"{row.row_id}: strict release readiness requires artifact to be an existing repo-relative path "
                "or HTTPS URL."
            )
        if (
            strict
            and row.status in {"passed", "waived"}
            and _artifact_is_github_actions_run(row.artifact)
            and not _artifact_is_ci_evidence(
                row.artifact,
                artifact_root,
                expected_repo=expected_repo,
                expected_run_id=expected_run_id,
            )
        ):
            errors.append(
                f"{row.row_id}: strict release readiness requires GitHub Actions URLs to point to "
                "the current repository and current CI run."
            )
        if (
            strict
            and row.row_id.startswith(P0_PREFIX)
            and row.status in {"passed", "waived"}
            and not _artifact_is_ci_evidence(
                row.artifact,
                artifact_root,
                expected_repo=expected_repo,
                expected_run_id=expected_run_id,
            )
        ):
            errors.append(
                f"{row.row_id}: strict P0 readiness requires artifact to point to CI-generated evidence, "
                "such as a GitHub Actions run URL for this repository or CI artifact path."
            )
        if strict and row.status == "waived":
            waiver_error = _waiver_error(row)
            if waiver_error:
                errors.append(f"{row.row_id}: {waiver_error}")

    if strict and dashboard_text:
        candidate = _candidate_commit_from_dashboard(dashboard_text)
        if not candidate:
            errors.append("Strict release readiness requires a Candidate commit in the dashboard.")
        elif current_sha and not _sha_matches(candidate, current_sha):
            errors.append(
                f"Current candidate commit {candidate} does not match checked-out HEAD {current_sha[:8]}."
            )
        current_evidence = artifact_root / "docs" / "release" / "current-release-evidence.md"
        if not current_evidence.exists():
            errors.append("Strict release readiness requires docs/release/current-release-evidence.md.")
        else:
            evidence_text = current_evidence.read_text(encoding="utf-8")
            evidence_commit = _current_evidence_commit(evidence_text)
            if not evidence_commit:
                errors.append("Current release evidence is missing Commit SHA.")
            elif current_sha and not _sha_matches(evidence_commit, current_sha):
                errors.append(
                    f"Current release evidence commit {evidence_commit} does not match checked-out HEAD {current_sha[:8]}."
                )
            evidence_run_id = _current_evidence_run_id(evidence_text)
            if expected_run_id and evidence_run_id and evidence_run_id != expected_run_id:
                errors.append(
                    f"Current release evidence run id {evidence_run_id} does not match GITHUB_RUN_ID {expected_run_id}."
                )
            evidence_ci_status = _current_evidence_summary_value(evidence_text, "CI status")
            if evidence_ci_status != "machine_gates_passed":
                errors.append(
                    "Current release evidence CI status must be machine_gates_passed for strict readiness; "
                    f"got {evidence_ci_status or 'missing'}."
                )
            evidence_worktree_status = _current_evidence_summary_value(evidence_text, "Worktree status")
            if evidence_worktree_status != "clean":
                errors.append(
                    "Current release evidence worktree status must be clean for strict readiness; "
                    f"got {evidence_worktree_status or 'missing'}."
                )
            manual_status = _current_evidence_summary_value(evidence_text, "Manual sign-off status")
            if manual_status not in STRICT_ACCEPTED_MANUAL_SIGNOFF_STATUSES:
                errors.append(
                    "Current release evidence manual sign-off status must record RC/release owner approval; "
                    f"got {manual_status or 'missing'}."
                )
            owner_signature = _current_evidence_summary_value(evidence_text, "Owner signature")
            if not owner_signature or owner_signature == "PENDING_RELEASE_OWNER_SIGNATURE":
                errors.append("Current release evidence owner signature is pending or missing.")

    return errors, warnings


def _artifact_is_verifiable(artifact: str, artifact_root: Path) -> bool:
    value = _artifact_link_target(artifact)
    if not value or value.upper() == "TBD":
        return False
    parsed = urlparse(value)
    if parsed.scheme in {"https"} and parsed.netloc:
        return True
    if parsed.scheme:
        return False
    candidate = (artifact_root / value).resolve()
    try:
        candidate.relative_to(artifact_root.resolve())
    except ValueError:
        return False
    return candidate.exists()


def _artifact_link_target(artifact: str) -> str:
    value = artifact.strip()
    markdown_link = re.search(r"\[[^\]]+\]\(([^)]+)\)", value)
    return markdown_link.group(1).strip() if markdown_link else value


def _artifact_is_github_actions_run(artifact: str) -> bool:
    parsed = urlparse(_artifact_link_target(artifact))
    return parsed.scheme == "https" and parsed.netloc.lower() == "github.com" and "/actions/runs/" in parsed.path


def _artifact_is_ci_evidence(
    artifact: str,
    artifact_root: Path,
    *,
    expected_repo: str = "",
    expected_run_id: str = "",
) -> bool:
    value = _artifact_link_target(artifact)
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc and "/actions/runs/" in parsed.path:
        if parsed.netloc.lower() != "github.com":
            return False
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) < 5 or path_parts[2] != "actions" or path_parts[3] != "runs":
            return False
        repo = "/".join(path_parts[:2]).lower()
        run_id = path_parts[4]
        if not expected_repo or repo != expected_repo.lower():
            return False
        if expected_run_id and run_id != expected_run_id:
            return False
        return True
    if parsed.scheme:
        return False
    normalized = value.replace("\\", "/").lstrip("./")
    if normalized == "docs/release/current-release-evidence.md":
        return True
    if not normalized.startswith(CI_ARTIFACT_PATH_PREFIXES):
        return False
    candidate = (artifact_root / value).resolve()
    try:
        candidate.relative_to(artifact_root.resolve())
    except ValueError:
        return False
    return candidate.exists()


def _candidate_commit_from_dashboard(markdown: str) -> str:
    match = re.search(r"\|\s*Candidate commit\s*\|\s*`?([0-9a-fA-F]{7,40})`?\s*\|", markdown)
    return match.group(1) if match else ""


def _current_evidence_commit(markdown: str) -> str:
    match = re.search(r"(?im)^-\s*Commit SHA:\s*`?([0-9a-fA-F]{7,40})`?\s*$", markdown)
    return match.group(1) if match else ""


def _current_evidence_run_id(markdown: str) -> str:
    match = re.search(r"(?im)^-\s*Run id:\s*`?([0-9]+)`?\s*$", markdown)
    return match.group(1) if match else ""


def _current_evidence_summary_value(markdown: str, label: str) -> str:
    escaped = re.escape(label)
    match = re.search(rf"(?im)^-\s*{escaped}:\s*(.+?)\s*$", markdown)
    if not match:
        return ""
    return match.group(1).strip().strip("`")


def _sha_matches(candidate: str, current_sha: str) -> bool:
    shorter = min(len(candidate), len(current_sha))
    return shorter >= 7 and candidate[:shorter].lower() == current_sha[:shorter].lower()


def _git_head_sha(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _git_remote_github_repo(root: Path) -> str:
    try:
        remote = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""
    match = re.search(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$", remote)
    return match.group(1) if match else ""


def _waiver_error(row: ReadinessRow) -> str:
    text = f"{row.expiry} {row.notes}"
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if not match:
        return "waived row requires an ISO expiry date."
    try:
        expiry = date.fromisoformat(match.group(1))
    except ValueError:
        return "waived row expiry date is invalid."
    if expiry < date.today():
        return "waived row expiry date has passed."
    notes = row.notes.casefold()
    if "reason" not in notes:
        return "waived row notes require a reason."
    if not _has_follow_up_reference(row.notes):
        return "waived row notes require an explicit follow-up issue reference."
    return ""


def _has_follow_up_reference(notes: str) -> bool:
    if ISSUE_URL_RE.search(notes):
        return True
    return bool(
        re.search(rf"(?:^|[\s(:]){ISSUE_KEY_RE}(?:$|[\s).,;])", notes, re.IGNORECASE)
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dashboard", default="docs/release/release-readiness-dashboard.md"
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--rc-release",
        action="store_true",
        help="Require every RR-P0 row to be passed; waivers are allowed only for scoped maintenance packaging.",
    )
    args = parser.parse_args()

    dashboard_path = Path(args.dashboard)
    if not dashboard_path.exists():
        print(
            json.dumps(
                {"ok": False, "error": f"Dashboard not found: {dashboard_path}"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    dashboard_text = dashboard_path.read_text(encoding="utf-8")
    rows = parse_rows(dashboard_text)
    resolved_dashboard = dashboard_path.resolve()
    artifact_root = (
        resolved_dashboard.parents[2]
        if len(resolved_dashboard.parents) > 2
        else Path.cwd()
    )
    errors, warnings = validate(
        rows,
        strict=args.strict or args.rc_release,
        rc_release=args.rc_release,
        artifact_root=artifact_root,
        dashboard_text=dashboard_text,
    )
    p0_rows = [row for row in rows if row.row_id.startswith(P0_PREFIX)]
    summary = {
        "ok": not errors,
        "strict": args.strict,
        "rc_release": args.rc_release,
        "dashboard": str(dashboard_path),
        "rows": len(rows),
        "p0_total": len(p0_rows),
        "p0_passed": sum(1 for row in p0_rows if row.status == "passed"),
        "p0_waived": sum(1 for row in p0_rows if row.status == "waived"),
        "p0_blocked": sum(1 for row in p0_rows if row.status == "blocked"),
        "p0_in_progress": sum(1 for row in p0_rows if row.status == "in_progress"),
        "warnings": warnings,
        "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
