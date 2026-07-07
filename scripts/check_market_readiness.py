#!/usr/bin/env python3
"""Validate the commercial launch dashboard and pricing source-of-truth."""

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
STRICT_ALLOWED_STATUSES = {"passed", "waived"}
ROW_RE = re.compile(r"^\|\s*(MR-P0-[^|]+?)\s*\|")
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
class MarketRow:
    row_id: str
    area: str
    status: str
    artifact: str
    owner: str
    notes: str


def _split_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_rows(markdown: str) -> list[MarketRow]:
    rows: list[MarketRow] = []
    for line in markdown.splitlines():
        if not ROW_RE.match(line):
            continue
        cells = _split_row(line)
        if len(cells) < 7:
            continue
        rows.append(
            MarketRow(
                row_id=cells[0],
                area=cells[1],
                status=cells[3].strip("`").lower(),
                artifact=cells[4],
                owner=cells[5],
                notes=cells[6],
            )
        )
    return rows


def validate(
    rows: list[MarketRow], *, strict: bool, paid_launch: bool = False, artifact_root: Path | None = None
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    artifact_root = artifact_root or Path.cwd()
    if not rows:
        return ["No MR-P0 stop-sell rows found."], warnings

    for row in rows:
        if row.status not in ALLOWED_STATUSES:
            errors.append(f"{row.row_id}: invalid status '{row.status}'.")
            continue
        if row.status in STRICT_ALLOWED_STATUSES:
            if (
                not row.owner
                or row.owner.upper() == "TBD"
                or "TBD" in row.owner.upper()
            ):
                errors.append(f"{row.row_id}: {row.status} row requires a named owner.")
            if not row.artifact or row.artifact.upper() == "TBD":
                errors.append(
                    f"{row.row_id}: {row.status} row requires an artifact/link label."
                )
        if row.status == "waived":
            waiver_error = _waiver_error(row)
            if waiver_error:
                errors.append(f"{row.row_id}: {waiver_error}")
        if strict and row.status == "passed" and not _artifact_is_verifiable(row.artifact, artifact_root):
            errors.append(
                f"{row.row_id}: strict market readiness requires passed rows to point to an existing "
                "repo-relative artifact path or HTTPS URL."
            )
        if row.status not in STRICT_ALLOWED_STATUSES:
            warnings.append(f"{row.row_id}: stop-sell row is {row.status}.")
            if strict:
                errors.append(
                    f"{row.row_id}: strict market readiness requires passed or waived, got {row.status}."
                )
        if paid_launch and row.status != "passed":
            errors.append(
                f"{row.row_id}: paid launch requires passed commercial evidence; "
                f"{row.status} is only allowed for no-sale maintenance packaging."
            )
    return errors, warnings


def _waiver_error(row: MarketRow) -> str:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", row.notes)
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


def _artifact_is_verifiable(artifact: str, artifact_root: Path) -> bool:
    value = artifact.strip()
    if not value or value.upper() == "TBD":
        return False
    markdown_link = re.search(r"\[[^\]]+\]\(([^)]+)\)", value)
    if markdown_link:
        value = markdown_link.group(1).strip()
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.netloc:
        return True
    if parsed.scheme:
        return False
    candidate = (artifact_root / value).resolve()
    try:
        candidate.relative_to(artifact_root.resolve())
    except ValueError:
        return False
    return candidate.exists()


def validate_sources(repo_root: Path) -> list[str]:
    errors: list[str] = []
    required = [
        repo_root / "LICENSE",
        repo_root / "docs" / "pricing.md",
        repo_root / "docs" / "business" / "pricing.md",
        repo_root / "docs" / "business" / "license-operations.md",
        repo_root / "docs" / "business" / "commercial-operations.md",
        repo_root / "docs" / "business" / "payment-tax-operations.md",
        repo_root / "docs" / "business" / "support-refund-operations.md",
        repo_root / "docs" / "business" / "public-claims-register.md",
        repo_root / "docs" / "business" / "support-privacy-operations.md",
        repo_root / "docs" / "legal" / "commercial-legal-approval-checklist.md",
        repo_root / "docs" / "legal" / "legal-source-register.md",
        repo_root / "docs" / "legal" / "commercial-legal-risk-memo.md",
        repo_root / "docs" / "legal" / "README.md",
        repo_root / "scripts" / "license_admin.py",
    ]
    for path in required:
        if not path.exists():
            errors.append(
                f"Required commercial source is missing: {path.relative_to(repo_root)}"
            )

    package_path = repo_root / "package.json"
    try:
        package = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"Unable to read package license metadata: {exc}")
    else:
        if package.get("license") != "BUSL-1.1":
            errors.append("package.json license must be BUSL-1.1.")
        if package.get("scripts", {}).get("license:admin") != "python scripts/license_admin.py":
            errors.append("package.json must expose the offline license:admin command.")
        if package.get("scripts", {}).get("evidence:commercial-operations-verify") != (
            "python scripts/verify_commercial_operations_evidence.py"
        ):
            errors.append("package.json must expose the commercial operations evidence verifier.")
        if package.get("scripts", {}).get("evidence:commercial-operations-seal") != (
            "python scripts/seal_commercial_operations_evidence.py"
        ):
            errors.append("package.json must expose the commercial operations evidence sealing helper.")

    pricing_path = repo_root / "docs" / "pricing.md"
    pointer_path = repo_root / "docs" / "business" / "pricing.md"
    if (
        pricing_path.exists()
        and "不构成公开报价或购买要约" not in pricing_path.read_text(encoding="utf-8")
    ):
        errors.append(
            "docs/pricing.md must state that internal pricing is not a public offer."
        )
    if pointer_path.exists():
        pointer = pointer_path.read_text(encoding="utf-8")
        if "../pricing.md" not in pointer:
            errors.append(
                "docs/business/pricing.md must point to the canonical docs/pricing.md."
            )
        if "entitlement gating 尚未实现" in pointer:
            errors.append(
                "docs/business/pricing.md contains stale entitlement implementation claims."
            )
    env_example = repo_root / ".env.example"
    if env_example.exists():
        env_text = env_example.read_text(encoding="utf-8")
        if "LENGRVIS_COMMERCIAL_RELEASE=false" not in env_text:
            errors.append(".env.example must document the commercial release profile gate.")
        if "LENGRVIS_LICENSE_PRIVATE_KEY=" in env_text:
            errors.append(".env.example must never define a runtime license private-key variable.")
    support_runbook = repo_root / "docs" / "business" / "support-privacy-operations.md"
    if support_runbook.exists():
        support_text = support_runbook.read_text(encoding="utf-8")
        for marker in ("Diagnostic package handling", "Data-subject and deletion requests", "Release rehearsal"):
            if marker not in support_text:
                errors.append(f"Support/privacy runbook is missing required section: {marker}.")
    operations_runbook = repo_root / "docs" / "business" / "commercial-operations.md"
    if operations_runbook.exists():
        operations_text = operations_runbook.read_text(encoding="utf-8")
        for marker in (
            "commercial-operations-evidence-reviewed",
            "npm run evidence:commercial-operations-verify",
            "npm run evidence:commercial-operations-seal",
            "法务、税务、收款",
            "payment-tax-operations.md",
            "support-refund-operations.md",
            "public-claims-register.md",
            "commercial-legal-approval-checklist.md",
        ):
            if marker not in operations_text:
                errors.append(f"Commercial operations runbook is missing required marker: {marker}.")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dashboard", default="docs/business/market-readiness.md")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--paid-launch",
        action="store_true",
        help="Require every MR-P0 row to be passed; waivers are allowed only for no-sale maintenance releases.",
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
    errors, warnings = validate(
        rows,
        strict=args.strict or args.paid_launch,
        paid_launch=args.paid_launch,
        artifact_root=Path.cwd(),
    )
    errors.extend(validate_sources(Path.cwd()))
    summary = {
        "ok": not errors,
        "strict": args.strict,
        "paid_launch": args.paid_launch,
        "dashboard": str(dashboard_path),
        "p0_total": len(rows),
        "p0_passed": sum(row.status == "passed" for row in rows),
        "p0_waived": sum(row.status == "waived" for row in rows),
        "p0_blocked": sum(row.status == "blocked" for row in rows),
        "p0_in_progress": sum(row.status == "in_progress" for row in rows),
        "warnings": warnings,
        "errors": errors,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
