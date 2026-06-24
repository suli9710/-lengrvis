from __future__ import annotations

import importlib.util
import sys
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
| MR-P0-002 | Legal | evidence | passed | legal-review | alice | approved |
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
    errors, _ = mod.validate(mod.parse_rows(SAMPLE), strict=True)
    assert any("MR-P0-001" in error for error in errors)
    assert not any("MR-P0-002" in error for error in errors)


def test_passed_row_requires_owner_and_artifact() -> None:
    sample = SAMPLE.replace("legal-review | alice", "TBD | TBD")
    errors, _ = mod.validate(mod.parse_rows(sample), strict=False)
    assert any("MR-P0-002" in error and "owner" in error for error in errors)
    assert any("MR-P0-002" in error and "artifact" in error for error in errors)


def test_repository_commercial_sources_are_consistent() -> None:
    assert mod.validate_sources(REPO_ROOT) == []
