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
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ALLOWED_STATUSES = {"blocked", "in_progress", "passed", "waived"}
STRICT_ALLOWED_P0_STATUSES = {"passed", "waived"}
P0_PREFIX = "RR-P0-"
ROW_RE = re.compile(r"^\|\s*(RR-[^|]+?)\s*\|(?P<body>.*)\|\s*$")
CI_ARTIFACT_PATH_PREFIXES = (
    ".tmp/qa-evidence/",
    ".tmp/release-evidence-packet/",
    ".tmp/packaging-smoke/",
    "build/",
    "desktop/release/",
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
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    artifact_root = artifact_root or Path.cwd()
    if not rows:
        errors.append("No readiness rows found.")
        return errors, warnings

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
            and row.row_id.startswith(P0_PREFIX)
            and row.status in {"passed", "waived"}
            and not _artifact_is_ci_evidence(row.artifact, artifact_root)
        ):
            errors.append(
                f"{row.row_id}: strict P0 readiness requires artifact to point to CI-generated evidence, "
                "such as a GitHub Actions run URL or CI artifact path."
            )
        if strict and row.status == "waived":
            waiver_error = _waiver_error(row)
            if waiver_error:
                errors.append(f"{row.row_id}: {waiver_error}")

    return errors, warnings


def _artifact_is_verifiable(artifact: str, artifact_root: Path) -> bool:
    value = artifact.strip()
    if not value or value.upper() == "TBD":
        return False
    markdown_link = re.search(r"\[[^\]]+\]\(([^)]+)\)", value)
    if markdown_link:
        value = markdown_link.group(1).strip()
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


def _artifact_is_ci_evidence(artifact: str, artifact_root: Path) -> bool:
    value = artifact.strip()
    markdown_link = re.search(r"\[[^\]]+\]\(([^)]+)\)", value)
    if markdown_link:
        value = markdown_link.group(1).strip()
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc and "/actions/runs/" in parsed.path:
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
    if "follow-up" not in notes and "followup" not in notes and "issue" not in notes:
        return "waived row notes require a follow-up issue."
    return ""


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

    rows = parse_rows(dashboard_path.read_text(encoding="utf-8"))
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
