from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_market_readiness.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_market_readiness", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()

SAMPLE = """
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Identity | evidence | blocked | TBD | TBD | open |
| MR-P0-002 | Legal | evidence | passed | https://github.com/example/repo/actions/runs/123 | alice | approved |
"""


def test_parse_market_rows() -> None:
    rows = mod.parse_rows(SAMPLE)
    assert [row.row_id for row in rows] == ["MR-P0-001", "MR-P0-002"]
    assert rows[1].owner == "alice"


def test_non_strict_reports_open_stop_sell_rows() -> None:
    errors, warnings = mod.validate(mod.parse_rows(SAMPLE), strict=False)
    assert errors == []
    assert any("MR-P0-001" in warning for warning in warnings)


def test_strict_fails_open_stop_sell_rows() -> None:
    errors, _ = mod.validate(mod.parse_rows(SAMPLE), strict=True, artifact_root=REPO_ROOT)
    assert any("MR-P0-001" in error for error in errors)
    assert not any("MR-P0-002" in error for error in errors)


def test_passed_row_requires_owner_and_artifact() -> None:
    sample = SAMPLE.replace("https://github.com/example/repo/actions/runs/123 | alice", "TBD | TBD")
    errors, _ = mod.validate(mod.parse_rows(sample), strict=False)
    assert any("MR-P0-002" in error and "owner" in error for error in errors)
    assert any("MR-P0-002" in error and "artifact" in error for error in errors)


def test_strict_passed_row_requires_verifiable_commercial_evidence() -> None:
    sample = SAMPLE.replace("https://github.com/example/repo/actions/runs/123", "commercial-loop-label-only")
    errors, _ = mod.validate(mod.parse_rows(sample), strict=True, artifact_root=REPO_ROOT)
    assert any("MR-P0-002" in error and "existing" in error for error in errors)


def test_paid_launch_rejects_waived_rows() -> None:
    future = date.today() + timedelta(days=21)
    note = (
        f"Waived until {future.isoformat()}; Waiver release: v0.1.2; "
        "reason: no-sale maintenance packaging. Follow-up issue: PAY-123."
    )
    sample = f"""
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Checkout | evidence | waived | docs/business/market-readiness.md | alice | {note} |
"""
    errors, _ = mod.validate(
        mod.parse_rows(sample),
        strict=True,
        paid_launch=True,
        artifact_root=REPO_ROOT,
        release_version="0.1.2",
    )
    assert any("paid launch requires passed" in error for error in errors)


def test_strict_accepts_unexpired_waiver_for_no_sale_packaging() -> None:
    future = date.today() + timedelta(days=21)
    note = (
        f"Waived until {future.isoformat()}; Waiver release: v0.1.2. "
        "reason: no-sale maintenance packaging. Follow-up issue: PAY-123."
    )
    sample = f"""
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Checkout | evidence | waived | docs/business/market-readiness.md | alice | {note} |
"""
    errors, _ = mod.validate(
        mod.parse_rows(sample),
        strict=True,
        artifact_root=REPO_ROOT,
        release_version="0.1.2",
    )

    assert errors == []


def test_strict_rejects_waiver_scoped_to_a_different_release_version() -> None:
    future = date.today() + timedelta(days=21)
    note = (
        f"Waived until {future.isoformat()}; Waiver release: v0.1.1; "
        "reason: maintenance packaging only; this explicitly does not cover v0.1.2; Follow-up issue: PAY-123."
    )
    sample = f"""
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Checkout | evidence | waived | docs/business/market-readiness.md | alice | {note} |
"""

    errors, _ = mod.validate(
        mod.parse_rows(sample),
        strict=True,
        artifact_root=REPO_ROOT,
        release_version="0.1.2",
    )

    assert any("current release version v0.1.2" in error for error in errors)


def test_strict_cli_binds_waivers_to_package_version(tmp_path: Path) -> None:
    future = date.today() + timedelta(days=21)
    dashboard = tmp_path / "market-readiness.md"
    dashboard.write_text(
        f"""
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Checkout | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future.isoformat()}; Waiver release: v0.1.1; reason: maintenance packaging only; this explicitly does not cover v0.1.2; Follow-up issue: PAY-123. |
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--dashboard", str(dashboard), "--strict"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any("current release version v0.1.2" in error for error in payload["errors"])


def test_strict_waiver_requires_explicit_current_release_context() -> None:
    future = date.today() + timedelta(days=21)
    sample = f"""
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Checkout | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future.isoformat()}; Waiver release: v0.1.2; reason: maintenance packaging. Follow-up issue: PAY-123. |
"""

    errors, _ = mod.validate(mod.parse_rows(sample), strict=True, artifact_root=REPO_ROOT)

    assert any("current release version context" in error for error in errors)


def test_waived_rows_require_expiry_reason_and_followup() -> None:
    future = date.today() + timedelta(days=21)
    sample = """
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Checkout | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future}; follow-up issue: PAY-123. |
| MR-P0-002 | Tax | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future}; reason: no-sale maintenance packaging. |
| MR-P0-003 | Legal | evidence | waived | docs/business/market-readiness.md | alice | reason: no-sale maintenance packaging. Follow-up issue: PAY-123. |
""".format(future=future.isoformat())
    errors, _ = mod.validate(mod.parse_rows(sample), strict=True, artifact_root=REPO_ROOT)

    assert any("MR-P0-001" in error and "reason" in error for error in errors)
    assert any("MR-P0-002" in error and "follow-up" in error for error in errors)
    assert any("MR-P0-003" in error and "ISO expiry" in error for error in errors)


def test_waived_rows_reject_expired_or_invalid_iso_dates() -> None:
    sample = """
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Checkout | evidence | waived | docs/business/market-readiness.md | alice | Waived until 2020-01-01; reason: no-sale maintenance packaging. Follow-up issue: PAY-123. |
| MR-P0-002 | Tax | evidence | waived | docs/business/market-readiness.md | alice | Waived until 2026-99-99; reason: no-sale maintenance packaging. Follow-up issue: PAY-123. |
"""
    errors, _ = mod.validate(mod.parse_rows(sample), strict=True, artifact_root=REPO_ROOT)

    assert any("MR-P0-001" in error and "has passed" in error for error in errors)
    assert any("MR-P0-002" in error and "invalid" in error for error in errors)


def test_waived_rows_require_explicit_followup_reference() -> None:
    future = date.today() + timedelta(days=21)
    sample = f"""
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| MR-P0-001 | Checkout | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future.isoformat()}; Waiver release: v0.1.2; reason: known issue. |
| MR-P0-002 | Tax | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future.isoformat()}; Waiver release: v0.1.2; reason: no-sale maintenance packaging. Tracker: PAY-123 |
| MR-P0-003 | Legal | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future.isoformat()}; Waiver release: v0.1.2; reason: no-sale maintenance packaging. https://github.com/example/repo/issues/123 |
| MR-P0-004 | Support | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future.isoformat()}; Waiver release: v0.1.2; reason: known issue. https://example.com/docs |
| MR-P0-005 | Refunds | evidence | waived | docs/business/market-readiness.md | alice | Waived until {future.isoformat()}; Waiver release: v0.1.2; reason: known issue. Follow-up issue: collect paid evidence. |
"""
    errors, _ = mod.validate(
        mod.parse_rows(sample),
        strict=True,
        artifact_root=REPO_ROOT,
        release_version="0.1.2",
    )

    assert any("MR-P0-001" in error and "follow-up issue reference" in error for error in errors)
    assert any("MR-P0-004" in error and "follow-up issue reference" in error for error in errors)
    assert any("MR-P0-005" in error and "follow-up issue reference" in error for error in errors)
    assert not any("MR-P0-002" in error for error in errors)
    assert not any("MR-P0-003" in error for error in errors)


def test_repository_commercial_sources_are_consistent() -> None:
    assert mod.validate_sources(REPO_ROOT) == []


def test_repository_dashboard_does_not_reuse_a_previous_version_waiver() -> None:
    version, version_errors = mod.load_release_version(REPO_ROOT)
    assert version_errors == []
    rows = mod.parse_rows((REPO_ROOT / "docs/business/market-readiness.md").read_text(encoding="utf-8"))

    errors, warnings = mod.validate(
        rows,
        strict=False,
        artifact_root=REPO_ROOT,
        release_version=version,
    )

    assert errors == []
    assert warnings
    assert all(row.status != "waived" for row in rows)
