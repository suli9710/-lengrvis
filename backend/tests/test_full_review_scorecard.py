from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_full_review_scorecard.py"
SCORECARD_PATH = REPO_ROOT / "docs" / "qa" / "full-review-scorecard.md"
READINESS_PATH = REPO_ROOT / "docs" / "release" / "release-readiness-dashboard.md"
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RELEASE_CANDIDATE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-candidate.yml"
RELEASE_READINESS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-readiness.yml"
RELEASE_PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-publish.yml"
CURRENT_RELEASE_EVIDENCE_SCRIPT = REPO_ROOT / "scripts" / "generate_current_release_evidence.ps1"
DELIVERY_PIPELINE_DOC = REPO_ROOT / "docs" / "release" / "delivery-pipeline.md"
RELEASE_GATE_DOC = REPO_ROOT / "docs" / "qa" / "release-gate.md"
CURRENT_CANDIDATE_SHA = "307c968e421131fa7ce62afdadef404ff02e94a6"
CURRENT_CANDIDATE_RUN_ID = "local/manual"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_full_review_scorecard", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def test_full_review_scorecard_current_repo_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--allow-dirty"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["score"]["total"] == 94
    assert payload["score"]["max"] == 100
    assert payload["score"]["rows"][0]["area"] == "Backend correctness and safety"
    assert payload["score"]["rows"][-1]["area"] == "Release readiness evidence"
    assert payload["release_readiness"]["p0_total"] == 7
    assert payload["release_readiness"]["p0_in_progress"] == 7
    assert payload["release_readiness"]["p0_rows"][0]["id"] == "RR-P0-001"


def test_scorecard_worktree_validation_rejects_untracked_source_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, check=True, capture_output=True, text=True)
    source = repo / "backend" / "app" / "untracked.py"
    source.parent.mkdir(parents=True)
    source.write_text("pass\n", encoding="utf-8")

    errors = mod.validate_worktree(repo)

    assert errors == ["full-review scorecard requires a clean worktree; found 1 change(s): ?? backend/app/untracked.py"]


def test_full_review_scorecard_parses_score_summary() -> None:
    scorecard = SCORECARD_PATH.read_text(encoding="utf-8")

    summary = mod.parse_score_summary(scorecard)

    assert summary["total"] == 94
    assert summary["max"] == 100
    assert sum(row["score"] for row in summary["rows"]) == 94
    assert sum(row["max"] for row in summary["rows"]) == 100


def test_full_review_scorecard_parses_readiness_summary() -> None:
    readiness = READINESS_PATH.read_text(encoding="utf-8")

    summary = mod.parse_readiness_summary(readiness)

    assert summary["p0_total"] == 7
    assert summary["p0_passed"] == 0
    assert summary["p0_in_progress"] == 7
    assert [row["id"] for row in summary["p0_rows"]] == [
        "RR-P0-001",
        "RR-P0-002",
        "RR-P0-003",
        "RR-P0-004",
        "RR-P0-005",
        "RR-P0-006",
        "RR-P0-007",
    ]


def test_full_review_scorecard_requires_agentic_threat_model_stop_ship_row() -> None:
    scorecard = SCORECARD_PATH.read_text(encoding="utf-8")
    readiness = READINESS_PATH.read_text(encoding="utf-8")
    without_threat_model = "\n".join(line for line in readiness.splitlines() if not line.startswith("| RR-P0-007 |"))

    errors = mod.validate_scorecard(scorecard, without_threat_model)

    assert any("missing required public Beta P0 rows: RR-P0-007" in error for error in errors)


def test_full_review_scorecard_rejects_100_when_rr_p0_rows_are_unfinished() -> None:
    scorecard = SCORECARD_PATH.read_text(encoding="utf-8")
    readiness = READINESS_PATH.read_text(encoding="utf-8")
    claimed_100 = (
        scorecard.replace("Total: 94 / 100.", "Total: 100 / 100.")
        .replace("| Maintainability | 14 / 15 |", "| Maintainability | 15 / 15 |")
        .replace("| Release readiness evidence | 10 / 15 |", "| Release readiness evidence | 15 / 15 |")
    )

    errors = mod.validate_scorecard(claimed_100, readiness)

    assert any("cannot claim 100/100" in error for error in errors)


def test_full_review_scorecard_rejects_100_when_rc_evidence_is_not_ready(tmp_path: Path) -> None:
    scorecard = SCORECARD_PATH.read_text(encoding="utf-8")
    readiness = READINESS_PATH.read_text(encoding="utf-8").replace(" in_progress |", " passed |")
    claimed_100 = (
        scorecard.replace("Total: 94 / 100.", "Total: 100 / 100.")
        .replace("| Maintainability | 14 / 15 |", "| Maintainability | 15 / 15 |")
        .replace("| Release readiness evidence | 10 / 15 |", "| Release readiness evidence | 15 / 15 |")
    )
    evidence_dir = tmp_path / "docs" / "release"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "current-release-evidence.md").write_text(
        "\n".join(
            [
                f"- Commit SHA: {CURRENT_CANDIDATE_SHA}",
                "- Release version: v0.1.2",
                "- Build identifier: v0.1.2 local/manual",
                f"- Run id: {CURRENT_CANDIDATE_RUN_ID}",
                "- CI status: machine_gates_failed_or_incomplete",
                "- Worktree status: clean",
                "- Manual sign-off status: manual_signoff_pending",
                "- Owner signature: PENDING_RELEASE_OWNER_SIGNATURE",
            ]
        ),
        encoding="utf-8",
    )

    errors = mod.validate_scorecard(
        claimed_100,
        readiness,
        artifact_root=tmp_path,
        current_sha=CURRENT_CANDIDATE_SHA,
        expected_repo="suli9710/-lengrvis",
        expected_run_id=CURRENT_CANDIDATE_RUN_ID,
    )

    assert any("strict RC release readiness" in error for error in errors)
    assert any("machine_gates_failed_or_incomplete" in error for error in errors)
    assert any("manual_signoff_pending" in error for error in errors)


def test_full_review_scorecard_rejects_total_mismatch() -> None:
    scorecard = SCORECARD_PATH.read_text(encoding="utf-8").replace("Total: 94 / 100.", "Total: 93 / 100.")
    readiness = READINESS_PATH.read_text(encoding="utf-8")

    errors = mod.validate_scorecard(scorecard, readiness)

    assert any("does not match row sum" in error for error in errors)


def test_full_review_scorecard_is_wired_into_release_gates() -> None:
    ci = CI_PATH.read_text(encoding="utf-8")
    release_candidate = RELEASE_CANDIDATE_WORKFLOW.read_text(encoding="utf-8")
    release_readiness = RELEASE_READINESS_WORKFLOW.read_text(encoding="utf-8")
    release_publish = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    current_release_evidence = CURRENT_RELEASE_EVIDENCE_SCRIPT.read_text(encoding="utf-8")
    delivery_pipeline_doc = DELIVERY_PIPELINE_DOC.read_text(encoding="utf-8")
    release_gate_doc = RELEASE_GATE_DOC.read_text(encoding="utf-8")
    readiness = READINESS_PATH.read_text(encoding="utf-8")

    assert "npm run review:scorecard" in ci
    assert "npm run review:scorecard" in release_candidate
    assert "npm run review:scorecard" in release_readiness
    assert "npm run review:scorecard" in release_publish
    assert "npm run review:scorecard" in current_release_evidence
    assert "`npm run review:scorecard` verifies the full-review scorecard before any" in delivery_pipeline_doc
    assert (
        "candidate-bound MCP and real-LLM quality evidence, maintainability gate, `review:scorecard`"
    ) in release_gate_doc
    assert "npm run review:scorecard" in readiness
    assert mod.validate_repo_wiring(REPO_ROOT) == []


def test_full_review_scorecard_rejects_missing_release_workflow_wiring(tmp_path: Path) -> None:
    for path, _phrase in mod.REQUIRED_WIRING:
        relative = path.relative_to(REPO_ROOT)
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    workflow = tmp_path / ".github" / "workflows" / "release-readiness.yml"
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace(
            "npm run review:scorecard", "python scripts/delivery_pipeline.py --plan-only"
        ),
        encoding="utf-8",
    )

    errors = mod.validate_repo_wiring(tmp_path)

    assert any("release-readiness.yml" in error and "review:scorecard" in error for error in errors)


def test_full_review_scorecard_rejects_missing_release_docs_wiring(tmp_path: Path) -> None:
    for path, _phrase in mod.REQUIRED_WIRING:
        relative = path.relative_to(REPO_ROOT)
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    delivery_doc = tmp_path / "docs" / "release" / "delivery-pipeline.md"
    delivery_doc.write_text(
        delivery_doc.read_text(encoding="utf-8").replace(
            "`npm run review:scorecard` verifies the full-review scorecard before any",
            "`npm run delivery:plan` prints the ordered delivery plan before any",
        ),
        encoding="utf-8",
    )

    errors = mod.validate_repo_wiring(tmp_path)

    assert any("delivery-pipeline.md" in error and "review:scorecard" in error for error in errors)
