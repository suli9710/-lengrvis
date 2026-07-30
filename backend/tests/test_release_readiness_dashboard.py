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
READINESS_PATH = REPO_ROOT / "docs" / "release" / "release-readiness-dashboard.md"
RELEASE_READINESS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-readiness.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_release_readiness_dashboard", SCRIPT_PATH)
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


def test_release_readiness_workflow_hard_gate_uses_rc_release():
    text = RELEASE_READINESS_WORKFLOW.read_text(encoding="utf-8")
    strict_section = text[text.index("Strict release readiness") : text.index("Strict market readiness")]

    assert "--rc-release" in strict_section
    assert "--strict" not in strict_section


def test_release_readiness_workflow_runs_review_scorecard_before_strict_gate():
    text = RELEASE_READINESS_WORKFLOW.read_text(encoding="utf-8")

    assert "npm run review:scorecard" in text
    assert text.index("npm run review:scorecard") < text.index("Strict release readiness")


def test_parse_rows_reads_p0_and_p1():
    rows = mod.parse_rows(SAMPLE)
    ids = {row.row_id for row in rows}
    assert {"RR-P0-001", "RR-P0-002", "RR-P1-001"} <= ids
    by_id = {row.row_id: row for row in rows}
    assert by_id["RR-P0-002"].status == "passed"
    assert by_id["RR-P0-002"].owner == "alice"
    assert by_id["RR-P0-002"].artifact == "https://github.com/example/repo/actions/runs/123"


def test_current_dashboard_exposes_all_public_beta_stop_ship_rows():
    rows = mod.parse_rows(READINESS_PATH.read_text(encoding="utf-8"))

    assert mod.validate_public_beta_gate_set(rows) == []
    assert {row.row_id for row in rows if row.row_id.startswith("RR-P0-")} == mod.REQUIRED_PUBLIC_BETA_P0_IDS


def test_public_beta_gate_set_rejects_missing_threat_model_row():
    rows = [row for row in mod.parse_rows(READINESS_PATH.read_text(encoding="utf-8")) if row.row_id != "RR-P0-007"]

    errors = mod.validate_public_beta_gate_set(rows)

    assert errors == ["Release readiness dashboard is missing required public Beta P0 rows: RR-P0-007"]


def test_non_strict_allows_blocked_p0_but_warns():
    rows = mod.parse_rows(SAMPLE)
    errors, warnings = mod.validate(rows, strict=False)
    assert errors == []
    assert any("RR-P0-001" in w for w in warnings)


def test_strict_fails_on_blocked_p0():
    rows = mod.parse_rows(SAMPLE)
    errors, _ = mod.validate(
        rows,
        strict=True,
        artifact_root=REPO_ROOT,
        expected_repo="example/repo",
    )
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


def test_strict_rejects_external_actions_repo():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-015 | X | ev | passed | https://github.com/other/repo/actions/runs/123 | alice | 2026-01-01 | n |\n"
    )
    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        artifact_root=REPO_ROOT,
        expected_repo="example/repo",
    )
    assert any("RR-P0-015" in e and "CI-generated evidence" in e for e in errors)


def test_strict_rejects_external_actions_repo_for_non_p0_rows():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P1-015 | X | ev | passed | https://github.com/other/repo/actions/runs/123 | alice | 2026-01-01 | n |\n"
    )
    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        artifact_root=REPO_ROOT,
        expected_repo="example/repo",
    )
    assert any("RR-P1-015" in e and "current repository and current CI run" in e for e in errors)


def test_strict_rejects_actions_run_id_that_is_not_current_ci():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-016 | X | ev | passed | https://github.com/example/repo/actions/runs/123 | alice | 2026-01-01 | n |\n"
    )
    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        artifact_root=REPO_ROOT,
        expected_repo="example/repo",
        expected_run_id="999",
    )
    assert any("RR-P0-016" in e and "CI-generated evidence" in e for e in errors)


def test_strict_requires_dashboard_candidate_commit(tmp_path):
    evidence_dir = tmp_path / "docs" / "release"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "current-release-evidence.md").write_text(
        "- Commit SHA: abcdef1234567890\n- Run id: 123\n",
        encoding="utf-8",
    )
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-017 | X | ev | passed | docs/release/current-release-evidence.md | alice | 2026-01-01 | n |\n"
    )
    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        artifact_root=tmp_path,
        dashboard_text="| Field | Value |\n| --- | --- |\n| Build id | 123 |\n",
        expected_repo="example/repo",
        current_sha="abcdef1234567890",
        expected_run_id="123",
    )
    assert any("Candidate commit" in e for e in errors)


def test_strict_requires_current_evidence_commit_to_match_head(tmp_path):
    evidence_dir = tmp_path / "docs" / "release"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "current-release-evidence.md").write_text(
        "- Commit SHA: 1234567890abcdef\n- Run id: 123\n",
        encoding="utf-8",
    )
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-018 | X | ev | passed | docs/release/current-release-evidence.md | alice | 2026-01-01 | n |\n"
    )
    dashboard = "| Field | Value |\n| --- | --- |\n| Candidate commit | `abcdef1234567890` |\n"
    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        artifact_root=tmp_path,
        dashboard_text=dashboard,
        expected_repo="example/repo",
        current_sha="abcdef1234567890",
        expected_run_id="123",
    )
    assert any("Current release evidence commit" in e for e in errors)


def test_strict_rejects_pending_rr_p0_006_current_evidence(tmp_path):
    evidence_dir = tmp_path / "docs" / "release"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "current-release-evidence.md").write_text(
        "\n".join(
            [
                "- Commit SHA: abcdef1234567890",
                "- Run id: 123",
                "- CI status: ci_results_unavailable",
                "- Worktree status: clean",
                "- Manual sign-off status: manual_signoff_pending",
                "- Owner signature: PENDING_RELEASE_OWNER_SIGNATURE",
            ]
        ),
        encoding="utf-8",
    )
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-006 | RC handoff and release-owner sign-off | ev | passed | docs/release/current-release-evidence.md | alice | 2026-01-01 | n |\n"
    )
    dashboard = "| Field | Value |\n| --- | --- |\n| Candidate commit | `abcdef1234567890` |\n"

    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        rc_release=True,
        artifact_root=tmp_path,
        dashboard_text=dashboard,
        expected_repo="example/repo",
        current_sha="abcdef1234567890",
        expected_run_id="123",
    )

    assert any("CI status" in e and "ci_results_unavailable" in e for e in errors)
    assert any("manual sign-off status" in e and "manual_signoff_pending" in e for e in errors)
    assert any("owner signature is pending" in e for e in errors)


def test_strict_accepts_signed_rr_p0_006_current_evidence(tmp_path):
    evidence_dir = tmp_path / "docs" / "release"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "current-release-evidence.md").write_text(
        "\n".join(
            [
                "- Commit SHA: abcdef1234567890",
                "- Run id: 123",
                "- CI status: machine_gates_passed",
                "- Worktree status: clean",
                "- Manual sign-off status: rc_signoff_recorded",
                "- Owner signature: release-owner-accepted-rc",
                "- Owner signature verification: verified",
                f"- Owner signature payload SHA-256: sha256:{'1' * 64}",
                f"- Owner signature key fingerprint: sha256:{'2' * 64}",
                "",
                "## Execution Commands",
                "",
                "| CI job | Command |",
                "| --- | --- |",
                "| Repo hygiene + dependency locks + review scorecard | `npm run review:scorecard` |",
            ]
        ),
        encoding="utf-8",
    )
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-006 | RC handoff and release-owner sign-off | ev | passed | docs/release/current-release-evidence.md | alice | 2026-01-01 | n |\n"
    )
    dashboard = "| Field | Value |\n| --- | --- |\n| Candidate commit | `abcdef1234567890` |\n"

    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        rc_release=True,
        artifact_root=tmp_path,
        dashboard_text=dashboard,
        expected_repo="example/repo",
        current_sha="abcdef1234567890",
        expected_run_id="123",
    )

    assert errors == []


def test_strict_rejects_current_release_evidence_without_scorecard_gate(tmp_path):
    evidence_dir = tmp_path / "docs" / "release"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "current-release-evidence.md").write_text(
        "\n".join(
            [
                "- Commit SHA: abcdef1234567890",
                "- Run id: 123",
                "- CI status: machine_gates_passed",
                "- Worktree status: clean",
                "- Manual sign-off status: rc_signoff_recorded",
                "- Owner signature: release-owner-accepted-rc",
                "",
                "## Execution Commands",
                "",
                "| CI job | Command |",
                "| --- | --- |",
                "| Repo hygiene + dependency locks | `npm run hygiene` |",
            ]
        ),
        encoding="utf-8",
    )
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-006 | RC handoff and release-owner sign-off | ev | passed | "
        "docs/release/current-release-evidence.md | alice | 2026-01-01 | n |\n"
    )
    dashboard = "| Field | Value |\n| --- | --- |\n| Candidate commit | `abcdef1234567890` |\n"

    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        rc_release=True,
        artifact_root=tmp_path,
        dashboard_text=dashboard,
        expected_repo="example/repo",
        current_sha="abcdef1234567890",
        expected_run_id="123",
    )

    assert any("full-review scorecard gate command" in e for e in errors)


def test_strict_rejects_dirty_current_release_evidence(tmp_path):
    evidence_dir = tmp_path / "docs" / "release"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "current-release-evidence.md").write_text(
        "\n".join(
            [
                "- Commit SHA: abcdef1234567890",
                "- Run id: 123",
                "- CI status: machine_gates_passed",
                "- Worktree status: dirty",
                "- Manual sign-off status: rc_signoff_recorded",
                "- Owner signature: release-owner-accepted-rc",
            ]
        ),
        encoding="utf-8",
    )
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-006 | RC handoff and release-owner sign-off | ev | passed | docs/release/current-release-evidence.md | alice | 2026-01-01 | n |\n"
    )
    dashboard = "| Field | Value |\n| --- | --- |\n| Candidate commit | `abcdef1234567890` |\n"

    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        rc_release=True,
        artifact_root=tmp_path,
        dashboard_text=dashboard,
        expected_repo="example/repo",
        current_sha="abcdef1234567890",
        expected_run_id="123",
    )

    assert any("worktree status must be clean" in e and "dirty" in e for e in errors)


def test_strict_waiver_requires_unexpired_expiry_reason_and_followup():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P0-012 | X | ev | waived | docs/release/release-readiness-dashboard.md | alice | TBD | later |\n"
    )
    errors, _ = mod.validate(mod.parse_rows(markdown), strict=True, artifact_root=REPO_ROOT)
    assert any("RR-P0-012" in e and "expiry" in e for e in errors)


def test_strict_waiver_requires_explicit_followup_reference():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P1-012 | X | ev | waived | docs/release/release-readiness-dashboard.md | alice | 2099-01-01 | Reason: known issue. |\n"
        "| RR-P1-013 | X | ev | waived | docs/release/release-readiness-dashboard.md | alice | 2099-01-01 | Reason: known issue. Follow-up issue: collect real evidence. |\n"
        "| RR-P1-014 | X | ev | waived | docs/release/release-readiness-dashboard.md | alice | 2099-01-01 | Reason: known issue. https://example.com/docs |\n"
    )
    errors, _ = mod.validate(mod.parse_rows(markdown), strict=True, artifact_root=REPO_ROOT)

    assert any("RR-P1-012" in e and "follow-up issue reference" in e for e in errors)
    assert any("RR-P1-013" in e and "follow-up issue reference" in e for e in errors)
    assert any("RR-P1-014" in e and "follow-up issue reference" in e for e in errors)


def test_strict_waiver_accepts_tracker_or_url_followup():
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| RR-P1-012 | X | ev | waived | docs/release/release-readiness-dashboard.md | alice | 2099-01-01 | Reason: maintenance only. Follow-up issue: REL-123 |\n"
        "| RR-P1-013 | X | ev | waived | docs/release/release-readiness-dashboard.md | alice | 2099-01-01 | Reason: maintenance only. https://github.com/example/repo/issues/123 |\n"
        "| RR-P1-014 | X | ev | waived | docs/release/release-readiness-dashboard.md | alice | 2099-01-01 | Reason: maintenance only. See #456 |\n"
    )
    errors, _ = mod.validate(mod.parse_rows(markdown), strict=True, artifact_root=REPO_ROOT)

    assert not any("RR-P1-012" in e for e in errors)
    assert not any("RR-P1-013" in e for e in errors)
    assert not any("RR-P1-014" in e for e in errors)


def test_rc_release_rejects_scoped_maintenance_waivers():
    note = "Reason: maintenance packaging only. Follow-up issue: REL-456."
    markdown = (
        "| ID | Area | Required evidence | Status | Artifact | Owner | Expiry | Notes |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        f"| RR-P0-014 | X | ev | waived | https://github.com/example/repo/actions/runs/123 | alice | 2099-01-01 | {note} |\n"
    )
    errors, _ = mod.validate(
        mod.parse_rows(markdown),
        strict=True,
        rc_release=True,
        artifact_root=REPO_ROOT,
        expected_repo="example/repo",
    )
    assert any("RR-P0-014" in e and "RC release requires passed P0 evidence" in e for e in errors)
