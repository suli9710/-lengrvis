"""Tests for scripts/check_release_readiness_dashboard.py.

The validator lives under scripts/ (not on the backend import path), so we load it
by file path with importlib.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_release_readiness_dashboard.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "check_release_readiness_dashboard", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec_module: on Python 3.13 dataclasses resolves the
    # frozen dataclass's (stringized) annotations via
    # sys.modules.get(cls.__module__).__dict__, which is None for a module
    # loaded by file path that was never inserted into sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


SAMPLE = """
| ID | Area | Required evidence | Status | Artifact / link label | Owner | Expiry / next review | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RR-P0-001 | Clean machine | ev | blocked | TBD | TBD | TBD | n |
| RR-P0-002 | Android | ev | passed | https://github.com/example/repo/actions/runs/123 | alice | 2026-01-01 | n |
| RR-P1-001 | Large files | change | in_progress | TBD | TBD | n |
"""


def test_script_path_exists():
    assert SCRIPT_PATH.exists(), f"missing validator at {SCRIPT_PATH}"


def test_parse_rows_reads_p0_and_p1():
    rows = mod.parse_rows(SAMPLE)
    ids = {row.row_id for row in rows}
    assert {"RR-P0-001", "RR-P0-002", "RR-P1-001"} <= ids
    by_id = {row.row_id: row for row in rows}
    assert by_id["RR-P0-002"].status == "passed"
    assert by_id["RR-P0-002"].owner == "alice"
    assert by_id["RR-P0-002"].artifact == "https://github.com/example/repo/actions/runs/123"


def test_non_strict_allows_blocked_p0_but_warns():
    rows = mod.parse_rows(SAMPLE)
    errors, warnings = mod.validate(rows, strict=False)
    assert errors == []
    assert any("RR-P0-001" in w for w in warnings)


def test_strict_fails_on_blocked_p0():
    rows = mod.parse_rows(SAMPLE)
    errors, _ = mod.validate(rows, strict=True, artifact_root=REPO_ROOT)
    assert any("RR-P0-001" in e for e in errors)
    # The passed P0 row must not be flagged in strict mode.
    assert not any("RR-P0-002" in e for e in errors)


def test_passed_row_requires_owner_and_artifact():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact / link label | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-009 | X | ev | passed | TBD | TBD | TBD | n |\n"
    )
    rows = mod.parse_rows(markdown)
    errors, _ = mod.validate(rows, strict=False)
    assert any("RR-P0-009" in e and "owner" in e for e in errors)
    assert any("RR-P0-009" in e and "artifact" in e for e in errors)


def test_empty_dashboard_reports_error():
    errors, _ = mod.validate([], strict=False)
    assert errors


def test_invalid_status_flagged():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-010 | X | ev | maybe | a | b | c | n |\n"
    )
    rows = mod.parse_rows(markdown)
    errors, _ = mod.validate(rows, strict=False)
    assert any("RR-P0-010" in e and "invalid status" in e for e in errors)


def test_strict_requires_verifiable_artifact_for_passed_rows():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-011 | X | ev | passed | artifact-label-only | alice | 2026-01-01 | n |\n"
    )
    errors, _ = mod.validate(mod.parse_rows(markdown), strict=True, artifact_root=REPO_ROOT)
    assert any("RR-P0-011" in e and "existing repo-relative path" in e for e in errors)


def test_strict_requires_p0_artifact_to_point_to_ci_evidence():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-013 | X | ev | passed | docs/release/release-readiness-dashboard.md | alice | 2026-01-01 | n |\n"
    )
    errors, _ = mod.validate(mod.parse_rows(markdown), strict=True, artifact_root=REPO_ROOT)
    assert any("RR-P0-013" in e and "CI-generated evidence" in e for e in errors)


def test_strict_waiver_requires_unexpired_expiry_reason_and_followup():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-012 | X | ev | waived | docs/release/release-readiness-dashboard.md | alice | TBD | later |\n"
    )
    errors, _ = mod.validate(mod.parse_rows(markdown), strict=True, artifact_root=REPO_ROOT)
    assert any("RR-P0-012" in e and "expiry" in e for e in errors)
