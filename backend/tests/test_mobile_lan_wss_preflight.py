from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tls_test_material import write_lan_tls_material


def _preflight_clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "LENGRVIS_BACKEND_HOST",
        "LENGRVIS_BACKEND_PORT",
        "LENGRVIS_LAN_PUBLIC_BASE_URL",
        "LENGRVIS_LAN_TLS_ENABLED",
        "LENGRVIS_LAN_TLS_CERT_FILE",
        "LENGRVIS_LAN_TLS_KEY_FILE",
    ):
        env.pop(key, None)
    return env


def _powershell() -> str:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")
    return powershell


def _run_preflight(
    project_root: Path,
    evidence_root: Path,
    *args: str,
) -> tuple[subprocess.CompletedProcess[str], dict[str, object], str]:
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_mobile_lan_wss_preflight.ps1"),
            "-Root",
            str(project_root),
            "-EvidenceRoot",
            str(evidence_root),
            *args,
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        env=_preflight_clean_env(),
        errors="replace",
        text=True,
        timeout=30,
    )
    summaries = list(evidence_root.rglob("evidence-summary.redacted.json"))
    assert len(summaries) == 1, result.stdout + result.stderr
    summary = json.loads(summaries[0].read_text(encoding="utf-8-sig"))
    checklist = next(evidence_root.rglob("real-device-evidence-checklist.redacted.md")).read_text(
        encoding="utf-8-sig"
    )
    return result, summary, checklist


def test_mobile_lan_wss_preflight_source_has_beginner_fail_closed_contract(project_root: Path) -> None:
    script = (project_root / "scripts" / "verify_mobile_lan_wss_preflight.ps1").read_text(
        encoding="utf-8"
    )
    matrix = (project_root / "docs" / "qa" / "real-device-mobile-matrix.md").read_text(
        encoding="utf-8"
    )

    for needle in (
        "real_device_evidence_status",
        "uncollected_fail_closed",
        "real_device_evidence_collected",
        "no_phone_preflight_claim",
        "not_real_device_pass",
        "approval_wss_evidence",
        "remote_screen_wss_evidence",
        "remote_input_wss_evidence",
        "certificate_trust_evidence",
        "grant_revoke_expiry_artifact_review",
        "Fail-closed real-device status",
        '"Approval WSS"',
        '"Remote Screen WSS"',
        '"Remote Input WSS"',
    ):
        assert needle in script

    for needle in (
        "Beginner Real-Device Collection Path",
        "real_device_evidence_status=uncollected_fail_closed",
        "real_device_evidence_collected=false",
        "no_phone_preflight_claim=not_real_device_pass",
        "`approval_wss_evidence`, `remote_screen_wss_evidence`, `remote_input_wss_evidence`",
        "`real_device_collection_checklist.approval_wss`",
        "`real_device_collection_checklist.remote_screen_wss`",
        "`real_device_collection_checklist.remote_input_wss`",
        "Leave every generated checklist item unchecked",
    ):
        assert needle in matrix


def test_ready_mobile_lan_wss_preflight_stays_fail_closed_until_real_device_artifacts_exist(
    project_root: Path,
    tmp_path: Path,
) -> None:
    cert, key = write_lan_tls_material(tmp_path)
    evidence_root = tmp_path / "mobile-lan-wss-preflight"
    result, summary, checklist = _run_preflight(
        project_root,
        evidence_root,
        "-BackendHost",
        "192.168.56.10",
        "-BackendPort",
        "9443",
        "-PublicBaseUrl",
        "https://lengrvis.local:9443",
        "-EnableLanTls",
        "-TlsCertFile",
        str(cert),
        "-TlsKeyFile",
        str(key),
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert summary["result"] == "ready_for_manual_real_device_collection_only"
    assert summary["real_device_evidence_status"] == "uncollected_fail_closed"
    assert summary["real_device_evidence_collected"] is False
    assert summary["no_phone_preflight_claim"] == "not_real_device_pass"
    assert "Fail-closed real-device status: uncollected_fail_closed" in output
    assert "This is still not a real-device pass" in output
    assert "no phone/emulator evidence has been collected by this script" in output

    manual_template = summary["manual_real_device_evidence_template"]
    assert manual_template["real_device_result"] == "uncollected"
    assert manual_template["real_device_evidence_status"] == "uncollected_fail_closed"
    assert manual_template["real_device_evidence_collected"] is False
    assert manual_template["no_phone_preflight_claim"] == "not_real_device_pass"
    assert manual_template["claim_controls"]["real_device_pass_claim_allowed"] is False
    assert manual_template["claim_controls"]["preflight_ready_is_pass"] is False

    fields = manual_template["fields"]
    for key in (
        "camera_qr_path_evidence",
        "actual_device_https_wss_evidence",
        "approval_wss_evidence",
        "remote_screen_wss_evidence",
        "remote_input_wss_evidence",
        "certificate_trust_evidence",
        "remote_input_grant_revoke_evidence",
        "remote_input_grant_expiry_evidence",
        "grant_revoke_expiry_artifact_review",
        "artifact_redaction_review",
    ):
        assert fields[key] == "uncollected"

    checklist_json = manual_template["real_device_collection_checklist"]
    for key in (
        "camera_qr",
        "actual_https_wss",
        "approval_wss",
        "remote_screen_wss",
        "remote_input_wss",
        "certificate_trust",
        "remote_input_grant_revoke_expiry",
        "screenshot_log_review",
    ):
        assert checklist_json[key]["status"] == "uncollected"
        assert checklist_json[key]["beginner_steps"]
        assert checklist_json[key]["reviewer_check"]

    assert "Actual approval WebSocket over WSS from that device" in summary["next_manual_evidence_needed"]
    assert "Actual remote screen WebSocket over WSS from that device" in summary["next_manual_evidence_needed"]
    assert "Actual remote input WebSocket over WSS from that device when input is in scope" in summary[
        "next_manual_evidence_needed"
    ]
    assert "Remote input revoke and expiry evidence" in "\n".join(summary["next_manual_evidence_needed"])

    assert "## Beginner Collection Path" in checklist
    assert "Do not mark any item complete while using only this computer" in checklist
    assert "### Approval WSS" in checklist
    assert "### Remote Screen WSS" in checklist
    assert "### Remote Input WSS" in checklist
    assert "Mobile screenshot/video showing the approval received from /ws/mobile/approvals over WSS" in checklist
    assert "Mobile screenshot/video showing /ws/remote/screen connected over WSS" in checklist
    assert "Mobile screenshot/video showing /ws/remote/input connected over WSS with remaining grant time" in checklist
    assert "real_device_evidence_status: uncollected_fail_closed" in checklist
    assert "real_device_evidence_collected=false" in checklist
    assert "no_phone_preflight_claim: not_real_device_pass" in checklist


def test_blocked_mobile_lan_wss_preflight_outputs_manual_checklist_without_pass_claim(
    project_root: Path,
    tmp_path: Path,
) -> None:
    evidence_root = tmp_path / "mobile-lan-wss-preflight"
    result, summary, checklist = _run_preflight(project_root, evidence_root)
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert summary["result"] == "blocked"
    assert summary["real_device_evidence_status"] == "uncollected_fail_closed"
    assert summary["real_device_evidence_collected"] is False
    assert summary["no_phone_preflight_claim"] == "not_real_device_pass"
    assert "[ready]" not in output
    assert "Fail-closed real-device status: uncollected_fail_closed" in output
    assert "This preflight does not use a phone, emulator, camera, QR scanner, or real WSS connection" in output

    manual_template = summary["manual_real_device_evidence_template"]
    assert manual_template["preflight_blocked"] is True
    assert manual_template["real_device_result"] == "uncollected"
    assert manual_template["claim_controls"]["real_device_pass_claim_allowed"] is False
    assert manual_template["claim_controls"]["preflight_ready_is_pass"] is False
    assert manual_template["fields"]["approval_wss_evidence"] == "uncollected"
    assert manual_template["fields"]["remote_screen_wss_evidence"] == "uncollected"
    assert manual_template["fields"]["remote_input_wss_evidence"] == "uncollected"
    assert manual_template["fields"]["certificate_trust_evidence"] == "uncollected"
    assert manual_template["fields"]["grant_revoke_expiry_artifact_review"] == "uncollected"
    assert manual_template["real_device_collection_checklist"]["approval_wss"]["status"] == "uncollected"
    assert manual_template["real_device_collection_checklist"]["remote_screen_wss"]["status"] == "uncollected"
    assert manual_template["real_device_collection_checklist"]["remote_input_wss"]["status"] == "uncollected"

    assert "Blocked Reasons Redacted" in checklist
    assert "### Approval WSS" in checklist
    assert "### Remote Screen WSS" in checklist
    assert "### Remote Input WSS" in checklist
    assert "real_device_pass_claim_allowed=false" in checklist
    assert "preflight_ready_is_pass=false" in checklist


def test_preflight_redacted_workspace_relative_paths_do_not_leak_sensitive_evidence_root(
    project_root: Path,
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    fake_org = "Contoso"
    fake_token_label = "token" + "-secret"
    fake_api_key = "sk" + "-proj-abc123456789"
    evidence_root = workspace_root / f"{fake_org}-{fake_token_label}-{fake_api_key}"
    result = subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "verify_mobile_lan_wss_preflight.ps1"),
            "-Root",
            str(workspace_root),
            "-EvidenceRoot",
            str(evidence_root),
        ],
        cwd=project_root,
        capture_output=True,
        encoding="utf-8",
        env=_preflight_clean_env(),
        errors="replace",
        text=True,
        timeout=30,
    )
    summaries = list(evidence_root.rglob("evidence-summary.redacted.json"))
    assert len(summaries) == 1, result.stdout + result.stderr
    summary_text = summaries[0].read_text(encoding="utf-8-sig")
    checklist_text = next(evidence_root.rglob("real-device-evidence-checklist.redacted.md")).read_text(
        encoding="utf-8-sig"
    )
    public_text = "\n".join([result.stdout, result.stderr, summary_text, checklist_text])

    assert result.returncode == 1, public_text
    assert "[redacted-org]" in public_text
    assert "[redacted-sensitive]" in public_text
    for secret in (fake_org, fake_token_label, fake_api_key):
        assert secret not in public_text
