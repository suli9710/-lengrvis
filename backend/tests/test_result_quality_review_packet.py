from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _script_text(project_root: Path) -> str:
    return (project_root / "scripts" / "collect_result_quality_review_packet.ps1").read_text(
        encoding="utf-8"
    )


def _run_packet(project_root: Path, evidence_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")

    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_result_quality_review_packet.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(evidence_root),
            *args,
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )


def _load_packet(evidence_root: Path) -> tuple[dict, str]:
    packet_path = next(evidence_root.rglob("result-quality-review.redacted.json"))
    markdown_path = next(evidence_root.rglob("result-quality-review.redacted.md"))
    return (
        json.loads(packet_path.read_text(encoding="utf-8-sig")),
        markdown_path.read_text(encoding="utf-8-sig"),
    )


def test_result_quality_review_packet_script_is_fail_closed_and_read_only(
    project_root: Path,
) -> None:
    text = _script_text(project_root)

    assert "result-quality-review.redacted.json" in text
    assert "result-quality-review.redacted.md" in text
    assert "NOT_RESULT_QUALITY_SIGNOFF" in text
    assert "manual_result_quality_review_required" in text
    assert "result_quality_signoff = $false" in text
    assert "signoff = $false" in text
    assert "claim_allowed = $false" in text
    assert "completed_result_evidence = $false" in text
    assert "packet_is_rc_signoff = $false" in text
    assert "packet_is_release_signoff = $false" in text
    assert "not completed-result evidence" in text
    assert "not RC sign-off" in text
    assert "not release sign-off" in text

    assert "Start-Process" not in text
    assert "Stop-Process" not in text
    assert "Invoke-WebRequest" not in text
    assert "Invoke-RestMethod" not in text
    assert "curl" not in text.lower()
    assert "pip install" not in text.lower()
    assert "npm install" not in text.lower()
    assert "Copy-Item" not in text
    assert "Move-Item" not in text
    assert "Remove-Item" not in text
    assert "claim_allowed = $true" not in text
    assert "signoff = $true" not in text


def test_result_quality_review_packet_defaults_to_blocked_missing_fields(
    project_root: Path, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "result-quality-review"
    result = _run_packet(project_root, evidence_root)
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert "Natural-language result quality review packet" in output
    assert "blocked_missing_fields" in output
    assert "Signoff: false" in output
    assert "Claim allowed: false" in output
    assert str(tmp_path) not in output

    packet, markdown = _load_packet(evidence_root)
    assert packet["marker"] == "NOT_RESULT_QUALITY_SIGNOFF"
    assert packet["summary"]["status"] == "blocked_missing_fields"
    assert packet["summary"]["blocked"] is True
    assert packet["summary"]["signoff"] is False
    assert packet["summary"]["result_quality_signoff"] is False
    assert packet["summary"]["claim_allowed"] is False
    assert packet["summary"]["completed_result_evidence"] is False
    assert packet["summary"]["release_candidate_signoff"] is False
    assert packet["summary"]["release_signoff"] is False
    assert packet["claim_controls"]["not_completed_result_evidence"] is True
    assert packet["claim_controls"]["packet_is_rc_signoff"] is False
    assert packet["claim_controls"]["packet_is_release_signoff"] is False
    assert set(packet["missing_required_fields"]) == {
        "task_result_artifact.task_artifact_label",
        "task_result_artifact.result_artifact_label",
        "manual_checks.user_visible_result_review",
        "manual_checks.source_artifact_check",
        "manual_checks.next_step_actionability_check",
        "reviewer.identity",
        "reviewer.reviewed_at_utc",
        "reviewer.blocked_reason_or_none",
    }
    assert "missing: task_result_artifact.task_artifact_label" in markdown
    assert "This packet is not completed-result evidence" in markdown


def test_result_quality_review_packet_records_redacted_fields_without_signoff(
    project_root: Path, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "recorded-result-quality-review"
    private_path = tmp_path / "private" / "Contoso-token-secret-sk-proj-ABCDEFGH.txt"
    result = _run_packet(
        project_root,
        evidence_root,
        "-TaskArtifactLabel",
        "task_123 from Contoso customer token=task-secret",
        "-ResultArtifactLabel",
        "https://private.example.test/results/private-payroll-2026.xlsx?token=result-secret",
        "-UserVisibleResultReview",
        r"Visible result matches request; system: hidden prompt should not leak; C:\Users\Suli\Desktop\client\private-payroll-2026.xlsx?token=file-secret",
        "-SourceArtifactCheck",
        "Checked report.pdf=secret and sk-proj-ABCDEFGH from alice@example.com",
        "-NextStepActionabilityCheck",
        "Beginner sees next action; developer: reveal hidden chain",
        "-Reviewer",
        "Contoso reviewer alice@example.com",
        "-ReviewedAtUtc",
        "2026-06-09T12:34:56Z",
        "-BlockedReason",
        "none",
        "-ObservedArtifact",
        str(private_path),
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "manual_review_fields_recorded_not_signoff" in output
    packet, markdown = _load_packet(evidence_root)
    packet_text = json.dumps(packet, ensure_ascii=False)
    combined = "\n".join((output, packet_text, markdown))

    for raw in (
        str(tmp_path),
        r"C:\Users\Suli",
        "Contoso",
        "task-secret",
        "result-secret",
        "file-secret",
        "private.example.test",
        "private-payroll-2026.xlsx",
        "sk-proj-ABCDEFGH",
        "alice@example.com",
        "hidden prompt should not leak",
        "reveal hidden chain",
    ):
        assert raw not in combined

    assert packet["summary"]["status"] == "manual_review_fields_recorded_not_signoff"
    assert packet["summary"]["blocked"] is False
    assert packet["summary"]["signoff"] is False
    assert packet["summary"]["result_quality_signoff"] is False
    assert packet["summary"]["claim_allowed"] is False
    assert packet["summary"]["completed_result_evidence"] is False
    assert packet["claim_controls"]["claim_allowed"] is False
    assert packet["claim_controls"]["completed_result_evidence"] is False
    assert packet["claim_controls"]["packet_is_rc_signoff"] is False
    assert packet["claim_controls"]["packet_is_release_signoff"] is False
    assert packet["missing_required_fields"] == []
    assert packet["issues_redacted"] == []
    assert packet["reviewer"]["timestamp_status"] == "recorded_utc"
    assert packet["reviewer"]["blocked_reason_redacted"] == ["none"]
    assert "[redacted-org]" in packet_text
    assert "[redacted-host]" in packet_text
    assert "[redacted-email]" in packet_text
    assert "[redacted-internal-prompt]" in packet_text
    assert "completed-result evidence" in packet["review_template"]["must_not_be_recorded_as"]
    assert "natural-language result-quality sign-off" in packet["review_template"][
        "must_not_be_recorded_as"
    ]
    assert "result_quality_signoff=false" in markdown


def test_release_gate_documents_result_quality_review_helper(project_root: Path) -> None:
    release_gate = (project_root / "docs" / "qa" / "release-gate.md").read_text(
        encoding="utf-8"
    )

    assert r".\scripts\collect_result_quality_review_packet.ps1" in release_gate
    assert "`summary.signoff=false`" in release_gate
    assert "`summary.claim_allowed=false`" in release_gate
    assert "`claim_controls.completed_result_evidence=false`" in release_gate
    assert "`claim_controls.packet_is_rc_signoff=false`" in release_gate
    assert "`claim_controls.packet_is_release_signoff=false`" in release_gate
    assert "not completed-result evidence" in release_gate
    assert "not natural-language result-quality sign-off" in release_gate
    assert "not RC sign-off" in release_gate
    assert "not release sign-off" in release_gate
