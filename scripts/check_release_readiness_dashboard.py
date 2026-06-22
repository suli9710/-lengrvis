#!/usr/bin/env python3
"""Validate the release readiness dashboard.

Default mode prints a machine-readable summary and exits 0 unless the dashboard is
missing or malformed. Strict mode fails when any P0 blocker is not passed or
explicitly waived.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ALLOWED_STATUSES = {"blocked", "in_progress", "passed", "waived"}
STRICT_ALLOWED_P0_STATUSES = {"passed", "waived"}
P0_PREFIX = "RR-P0-"
ROW_RE = re.compile(r"^\|\s*(RR-[^|]+?)\s*\|(?P<body>.*)\|\s*$")


@dataclass(frozen=True)
class ReadinessRow:
    row_id: str
    area: str
    status: str
    artifact: str
    owner: str
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
                notes=cells[-1] if cells else "",
            )
        )
    return rows


def validate(rows: list[ReadinessRow], *, strict: bool) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
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
                errors.append(f"{row.row_id}: {row.status} row requires an artifact/link label.")
        if row.row_id.startswith(P0_PREFIX) and row.status != "passed":
            warnings.append(f"{row.row_id}: stop-ship row is {row.status}.")
        if strict and row.row_id.startswith(P0_PREFIX) and row.status not in STRICT_ALLOWED_P0_STATUSES:
            errors.append(f"{row.row_id}: strict release readiness requires passed or waived, got {row.status}.")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard", default="docs/release/release-readiness-dashboard.md")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    dashboard_path = Path(args.dashboard)
    if not dashboard_path.exists():
        print(json.dumps({"ok": False, "error": f"Dashboard not found: {dashboard_path}"}, ensure_ascii=False, indent=2))
        return 2

    rows = parse_rows(dashboard_path.read_text(encoding="utf-8"))
    errors, warnings = validate(rows, strict=args.strict)
    p0_rows = [row for row in rows if row.row_id.startswith(P0_PREFIX)]
    summary = {
        "ok": not errors,
        "strict": args.strict,
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
