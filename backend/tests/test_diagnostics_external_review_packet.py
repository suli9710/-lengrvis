from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _powershell() -> str:
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell is not available")
    return shell


def _write_diagnostics_support_package(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-06-09T00:00:00+00:00",
                "diagnostics": {
                    "diagnostic_scope": "local_only",
                    "logs": [
                        {
                            "path": r"C:\Users\Suli\AppData\Local\Lengrvis\logs\backend.log",
                            "message": "Authorization: Bearer diagnostics-secret-token",
                        }
                    ],
                    "local_paths": {
                        "data_dir": r"C:\Users\Suli\AppData\Roaming\Lengrvis",
                        "database": r"C:\Users\Suli\AppData\Roaming\Lengrvis\lengrvis.db",
                    },
                    "task_traces": [
                        {
                            "task_id": "task-secret-123",
                            "prompt": "Read private-payroll-2026.xlsx",
                        }
                    ],
                    "model_traces": {
                        "provider": "local",
                        "hidden_prompt": "system: do not reveal",
                    },
                    "device_identifiers": {
                        "pairing_id": "pair-secret-123",
                        "lan_host": "suli-private-laptop.local",
                    },
                    "support_package_redaction": {
                        "schema_version": 1,
                        "applies_to": "diagnostics_export_payload",
                        "scope": "local_only",
                        "intended_audience": "trusted_support",
                        "public_safe": False,
                        "review_before_external_sharing": True,
                        "external_review": {
                            "schema_version": 1,
                            "status": "manual_review_required",
                            "required_before_external_sharing": True,
                            "public_safe": False,
                        },
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _run_review_packet(
    project_root: Path,
    evidence_root: Path,
    *,
    diagnostics_package_path: Path | None = None,
    diagnostics_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        _powershell(),
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(project_root / "scripts" / "collect_diagnostics_external_review_packet.ps1"),
        "-Root",
        str(project_root),
        "-EvidenceRoot",
        str(evidence_root),
    ]
    if diagnostics_package_path is not None:
        command.extend(["-DiagnosticsPackagePath", str(diagnostics_package_path)])
    if diagnostics_root is not None:
        command.extend(["-DiagnosticsRoot", str(diagnostics_root)])

    return subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
    )


def _load_packet(evidence_root: Path) -> tuple[dict[str, object], str]:
    json_path = next(evidence_root.rglob("diagnostics-external-review.redacted.json"))
    markdown_path = next(evidence_root.rglob("diagnostics-external-review.redacted.md"))
    return (
        json.loads(json_path.read_text(encoding="utf-8-sig")),
        markdown_path.read_text(encoding="utf-8-sig"),
    )


def test_diagnostics_external_review_packet_is_template_not_actual_content_review(
    project_root: Path, tmp_path: Path
) -> None:
    diagnostics_package = tmp_path / "exports" / "lengrvis-diagnostics-public-safety.json"
    _write_diagnostics_support_package(diagnostics_package)
    evidence_root = tmp_path / "review-evidence"

    result = _run_review_packet(
        project_root,
        evidence_root,
        diagnostics_package_path=diagnostics_package,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    packet, markdown = _load_packet(evidence_root)
    combined = "\n".join((output, json.dumps(packet), markdown))
    for raw in (
        str(tmp_path),
        "diagnostics-secret-token",
        "private-payroll-2026.xlsx",
        "task-secret-123",
        "pair-secret-123",
        "suli-private-laptop.local",
        "system: do not reveal",
    ):
        assert raw not in combined

    assert packet["summary"]["public_safe"] is False
    assert packet["summary"]["external_sharing_allowed"] is False
    assert packet["summary"]["claim_allowed"] is False
    assert packet["summary"]["review_fields_complete"] is False
    assert packet["summary"]["external_sharing_blocked"] is True
    assert packet["summary"]["separate_human_content_review_required"] is True
    assert packet["summary"]["actual_package_content_review_completed"] is False
    assert packet["summary"]["automated_template_only"] is True
    assert packet["review_scope"]["automated_redaction_template"] is True
    assert packet["review_scope"]["review_fields_complete"] is False
    assert packet["review_scope"]["external_sharing_blocked"] is True
    assert packet["review_scope"]["separate_human_content_review_required"] is True
    assert packet["review_scope"]["actual_package_content_review_completed"] is False
    assert packet["review_scope"]["automated_template_is_actual_package_content_review"] is False
    assert packet["claim_controls"]["public_safe"] is False
    assert packet["claim_controls"]["external_sharing_allowed"] is False
    assert packet["claim_controls"]["claim_allowed"] is False
    assert packet["claim_controls"]["helper_can_approve_public_safety"] is False
    assert packet["claim_controls"]["helper_can_authorize_external_sharing"] is False
    assert packet["claim_controls"]["external_sharing_blocked"] is True
    assert packet["claim_controls"]["separate_human_content_review_required"] is True
    assert packet["claim_controls"]["public_safe_approval_created"] is False

    input_package = packet["input_diagnostics_package"]
    assert input_package["actual_exported_package_path_label"] == (
        "lengrvis-diagnostics-public-safety.json"
    )
    assert packet["review_template"]["actual_exported_package_path_label"] == (
        "lengrvis-diagnostics-public-safety.json"
    )
    assert packet["review_template"]["reviewer_identity_redacted"] == "uncollected"
    assert packet["review_template"]["reviewed_at_utc"] == "uncollected"
    assert packet["review_template"]["review_fields_complete"] is False
    assert packet["review_template"]["external_sharing_blocked"] is True
    assert packet["review_template"]["separate_human_content_review_required"] is True
    assert "actual exported diagnostics package content review is uncollected" in packet[
        "review_template"
    ]["blocked_reason_redacted"]

    checklist = {item["id"]: item for item in packet["review_template"]["checklist"]}
    for checklist_id in (
        "actual_exported_package_path_label",
        "reviewed_logs",
        "reviewed_path_labels",
        "reviewed_task_traces",
        "reviewed_model_traces",
        "reviewed_device_identifiers",
        "reviewer_timestamp",
        "blocked_reason",
    ):
        assert checklist_id in checklist
        assert checklist[checklist_id]["required"] is True
        assert checklist[checklist_id]["reviewed"] is False

    assert checklist["actual_exported_package_path_label"][
        "actual_exported_package_path_label"
    ] == "lengrvis-diagnostics-public-safety.json"
    assert "External sharing allowed: false" in markdown
    assert "Claim allowed: false" in markdown
    assert "Actual package content review completed: false" in markdown
    assert "Review fields complete: false" in markdown
    assert "External sharing blocked: true" in markdown
    assert "Separate human content review required: true" in markdown
    assert "This template is automated redaction/checklist scaffolding" in markdown
    assert "reviewed_logs" in markdown
    assert "reviewed_model_traces" in markdown
    assert "reviewer_timestamp" in markdown
    assert "blocked_reason" in markdown
    assert '"public_safe": true' not in json.dumps(packet).lower()


def test_diagnostics_external_review_packet_missing_package_keeps_claims_blocked(
    project_root: Path, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "missing-review-evidence"
    missing_package = tmp_path / "private-sk-diagnostics-secret" / "missing.json"

    result = _run_review_packet(
        project_root,
        evidence_root,
        diagnostics_package_path=missing_package,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert str(tmp_path) not in output
    assert "private-sk-diagnostics-secret" not in output
    packet, markdown = _load_packet(evidence_root)

    assert packet["summary"]["status"] == "blocked_missing_diagnostics_package"
    assert packet["summary"]["public_safe"] is False
    assert packet["summary"]["external_sharing_allowed"] is False
    assert packet["summary"]["claim_allowed"] is False
    assert packet["summary"]["external_sharing_blocked"] is True
    assert packet["summary"]["separate_human_content_review_required"] is True
    assert packet["claim_controls"]["external_sharing_allowed"] is False
    assert packet["claim_controls"]["claim_allowed"] is False
    assert packet["claim_controls"]["external_sharing_blocked"] is True
    assert packet["claim_controls"]["separate_human_content_review_required"] is True
    assert packet["claim_controls"]["public_safe_approval_created"] is False
    assert packet["review_scope"]["actual_package_content_review_completed"] is False

    blocked_reasons = packet["review_template"]["blocked_reason_redacted"]
    assert "specified diagnostics package was not found" in blocked_reasons
    assert "actual exported diagnostics package path label is missing" in blocked_reasons
    assert "actual exported diagnostics package content review is uncollected" in blocked_reasons
    assert "reviewer identity and review timestamp are uncollected" in blocked_reasons
    checklist = {item["id"]: item for item in packet["review_template"]["checklist"]}
    assert checklist["blocked_reason"]["status"] == "blocked"
    assert checklist["blocked_reason"]["reviewed"] is False
    assert checklist["reviewer_timestamp"]["reviewer_identity_redacted"] == "uncollected"
    assert checklist["reviewer_timestamp"]["reviewed_at_utc"] == "uncollected"
    assert "Blocked reasons redacted" in markdown
    assert "external_sharing_allowed=false" in markdown
    assert "claim_allowed=false" in markdown


def test_diagnostics_external_review_packet_rejects_string_boolean_source_contract(
    project_root: Path, tmp_path: Path
) -> None:
    diagnostics_package = tmp_path / "exports" / "lengrvis-diagnostics-string-bools.json"
    _write_diagnostics_support_package(diagnostics_package)
    package = json.loads(diagnostics_package.read_text(encoding="utf-8"))
    redaction = package["diagnostics"]["support_package_redaction"]
    redaction["public_safe"] = "false"
    redaction["external_review"]["public_safe"] = "false"
    redaction["external_review"]["required_before_external_sharing"] = "true"
    diagnostics_package.write_text(
        json.dumps(package, ensure_ascii=False),
        encoding="utf-8",
    )
    evidence_root = tmp_path / "string-bool-review-evidence"

    result = _run_review_packet(
        project_root,
        evidence_root,
        diagnostics_package_path=diagnostics_package,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    packet, markdown = _load_packet(evidence_root)
    assert packet["summary"]["status"] == "blocked_contract_mismatch"
    assert packet["summary"]["public_safe"] is False
    assert packet["summary"]["external_sharing_allowed"] is False
    assert packet["summary"]["claim_allowed"] is False
    assert packet["summary"]["external_sharing_blocked"] is True
    assert packet["summary"]["separate_human_content_review_required"] is True
    assert packet["review_scope"]["actual_package_content_review_completed"] is False
    assert packet["source_redaction_contract"]["package_public_safe_observation"] == (
        "not_false_ignored"
    )
    assert packet["source_redaction_contract"]["external_review_public_safe_observation"] == (
        "not_false_ignored"
    )
    assert packet["source_redaction_contract"][
        "required_before_external_sharing_observation"
    ] == "not_required_in_input_but_required_by_template"
    issues = "\n".join(packet["issues_redacted"])
    assert "support package public-safe flag was not false in input" in issues
    assert "external review public-safe flag was not false in input" in issues
    assert "external review is not marked required before external sharing" in issues
    assert "External sharing allowed: false" in markdown
    assert "Claim allowed: false" in markdown


def test_release_gate_documents_diagnostics_review_claim_controls(project_root: Path) -> None:
    release_gate = (project_root / "docs" / "qa" / "release-gate.md").read_text(
        encoding="utf-8"
    )

    assert "`external_sharing_allowed=false`" in release_gate
    assert "`claim_allowed=false`" in release_gate
    assert "`review_scope.automated_redaction_template=true`" in release_gate
    assert "`review_scope.actual_package_content_review_completed=false`" in release_gate
    assert "`claim_controls.public_safe_approval_created=false`" in release_gate
    assert "`summary.review_fields_complete=false`" in release_gate
    assert "`summary.external_sharing_blocked=true`" in release_gate
    assert "`summary.separate_human_content_review_required=true`" in release_gate
    assert "actual exported package path label" in release_gate
    assert "logs/path labels/task traces/model traces/device identifiers" in release_gate
    assert "reviewer/timestamp" in release_gate
    assert "blocked reason" in release_gate
