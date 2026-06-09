from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _powershell() -> str:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is not available")
    return powershell


def _run_rc_handoff_template(
    project_root: Path,
    evidence_root: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "collect_rc_handoff_template.ps1"),
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


def _latest_packet(evidence_root: Path) -> tuple[dict[str, object], str]:
    runs = sorted(evidence_root.glob("run-*"))
    assert runs, "RC handoff helper did not create an evidence run directory"
    run_root = runs[-1]
    packet = json.loads(
        (run_root / "rc-handoff-template.redacted.json").read_text(encoding="utf-8")
    )
    markdown = (run_root / "rc-handoff-template.redacted.md").read_text(encoding="utf-8")
    return packet, markdown


def test_rc_handoff_template_missing_fields_fail_closed(
    project_root: Path, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "rc-handoff"
    result = _run_rc_handoff_template(project_root, evidence_root)

    assert result.returncode == 0, result.stderr
    packet, markdown = _latest_packet(evidence_root)

    summary = packet["summary"]
    assert summary["status"] == "manual_rc_handoff_required"
    assert summary["release_candidate_signoff"] is False
    assert summary["claim_allowed"] is False
    assert summary["template_is_rc_pass"] is False
    assert packet["signoff_controls"]["pass_defaults_remain_false"] is True

    missing = set(summary["missing_required_fields"])
    assert missing == {
        "candidate.commit_or_build_id",
        "candidate.platform",
        "artifact_labels",
        "gate_results.commands_and_exits",
        "strict_state_source",
        "manual_p1_checks",
        "waivers",
        "residual_risks",
    }
    assert packet["actionable_handoff"]["status"] == "manual_rc_handoff_required"
    assert "release-candidate pass" in packet["must_not_be_recorded_as"]
    assert "release_candidate_signoff=false" in markdown
    assert "claim_allowed=false" in markdown
    assert "NOT_RELEASE_CANDIDATE_SIGNOFF" in markdown


def test_rc_handoff_template_records_redacted_material_without_claiming_pass(
    project_root: Path, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "rc-handoff"
    result = _run_rc_handoff_template(
        project_root,
        evidence_root,
        "-CandidateCommit",
        "abc123def456",
        "-BuildId",
        "build-sk-proj-supersecret123456",
        "-Platform",
        "windows-x64",
        "-ArtifactLabel",
        r"C:\Users\Suli\Desktop\secret-build\Lengrvis-portable.zip?token=supersecret",
        "-GateCommand",
        (
            "npm run qa:gate -- --lan=https://10.0.0.42:9443/run?token=supersecret "
            "Cookie: session=rc-cookie-secret pairing code 123456"
        ),
        "-GateExit",
        "exit 0",
        "-StrictStateSource",
        str(project_root / "backend" / "app" / "orchestration" / "state_machine.py"),
        "-ManualP1Check",
        (
            "mobile LAN/WSS artifact reviewed; Authorization: Bearer abcsecret; "
            "Set-Cookie=session=rc-set-cookie-secret; one-time code: 654321"
        ),
        "-Waiver",
        "none; owner=contoso-release; reason=no waiver requested; expiry=not applicable",
        "-ResidualRisk",
        (
            "clean-machine signoff still separate; follow-up "
            "https://contoso.example/private?api_key=secret; otp 778899; "
            "pairingCode=pair-secret-0000"
        ),
    )

    assert result.returncode == 0, result.stderr
    packet, markdown = _latest_packet(evidence_root)
    serialized = json.dumps(packet, ensure_ascii=False) + markdown + result.stdout

    assert packet["summary"]["status"] == "manual_rc_handoff_recorded_unverified_by_this_helper"
    assert packet["summary"]["release_candidate_signoff"] is False
    assert packet["summary"]["claim_allowed"] is False
    assert packet["gate_results"]["commands_and_exits_count_match"] is True
    assert len(packet["gate_results"]["entries"]) == 1
    assert packet["strict_state_source"]["source"] == r"backend\app\orchestration\state_machine.py"
    assert packet["manual_p1_checks"]["entries"]
    assert packet["waivers"]["entries"]
    assert packet["residual_risks"]["entries"]

    forbidden_fragments = [
        "supersecret",
        "abcsecret",
        "sk-proj-supersecret",
        "C:\\Users\\Suli",
        "10.0.0.42",
        "contoso",
        "api_key=secret",
        "rc-cookie-secret",
        "rc-set-cookie-secret",
        "123456",
        "654321",
        "778899",
        "pair-secret-0000",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in serialized

    assert "release-candidate pass" in packet["must_not_be_recorded_as"]
    assert "claim_allowed=false" in markdown


def test_rc_handoff_template_rejects_placeholders_and_thin_values(
    project_root: Path, tmp_path: Path
) -> None:
    evidence_root = tmp_path / "rc-handoff"
    result = _run_rc_handoff_template(
        project_root,
        evidence_root,
        "-CandidateCommit",
        "<commit SHA>",
        "-BuildId",
        "<build id>",
        "-Platform",
        "<platform>",
        "-ArtifactLabel",
        "<redacted artifact label>",
        "-GateCommand",
        "<exact command>",
        "-GateExit",
        "<exit code/status>",
        "-StrictStateSource",
        "<state source label>",
        "-ManualP1Check",
        "todo",
        "-Waiver",
        "waiver requested",
        "-ResidualRisk",
        "todo",
    )

    assert result.returncode == 0, result.stderr
    packet, markdown = _latest_packet(evidence_root)
    summary = packet["summary"]
    assert summary["status"] == "manual_rc_handoff_required"
    assert summary["release_candidate_signoff"] is False
    assert summary["claim_allowed"] is False
    assert packet["signoff_controls"]["must_not_tag_publish_or_announce"] is True
    assert set(summary["missing_required_fields"]) == {
        "candidate.commit_or_build_id",
        "candidate.platform",
        "artifact_labels",
        "gate_results.commands_and_exits",
        "strict_state_source",
        "manual_p1_checks",
        "waivers",
        "residual_risks",
    }
    assert packet["candidate"]["commit_or_build_id_status"] == "missing_required_field"
    assert packet["candidate"]["platform_status"] == "missing_required_field"
    assert packet["artifacts"]["status"] == "missing_required_field"
    assert packet["gate_results"]["status"] == "manual_rc_handoff_required"
    assert packet["manual_p1_checks"]["status"] == "missing_required_field"
    assert "candidate.platform" in markdown
    assert "release_candidate_signoff=false" in markdown


@pytest.mark.parametrize(
    ("gate_commands", "gate_exits", "missing_entry"),
    [
        (["npm run qa:gate", "npm run release:check"], ["exit 0"], {"exit_status": "uncollected"}),
        (["npm run qa:gate"], ["exit 0", "exit 1"], {"command": "uncollected"}),
    ],
)
def test_rc_handoff_template_requires_one_exit_per_gate_command(
    project_root: Path,
    tmp_path: Path,
    gate_commands: list[str],
    gate_exits: list[str],
    missing_entry: dict[str, str],
) -> None:
    evidence_root = tmp_path / "rc-handoff"
    args = [
        "-CandidateCommit",
        "abc123def456",
        "-Platform",
        "windows-x64",
        "-ArtifactLabel",
        "Lengrvis-portable.zip",
        "-GateCommand",
        ";;".join(gate_commands),
        "-GateExit",
        ";;".join(gate_exits),
        "-StrictStateSource",
        "strict-state-machine",
        "-ManualP1Check",
        "P1 reviewed by release owner",
        "-Waiver",
        "none",
        "-ResidualRisk",
        "risk accepted by release owner",
    ]
    result = _run_rc_handoff_template(project_root, evidence_root, *args)

    assert result.returncode == 0, result.stderr
    packet, markdown = _latest_packet(evidence_root)
    assert packet["summary"]["status"] == "manual_rc_handoff_required"
    assert packet["summary"]["release_candidate_signoff"] is False
    assert packet["summary"]["claim_allowed"] is False
    assert "gate_results.commands_and_exits_count_match" in packet["summary"][
        "missing_required_fields"
    ]
    assert packet["gate_results"]["commands_and_exits_count_match"] is False
    assert packet["gate_results"]["commands_run_by_this_helper"] is False
    assert len(packet["gate_results"]["entries"]) == 2
    assert any(
        all(entry.get(key) == value for key, value in missing_entry.items())
        for entry in packet["gate_results"]["entries"]
    )
    assert "same order" in markdown
    assert "release_candidate_signoff=false" in markdown


def test_rc_handoff_template_entrypoint_is_documented(project_root: Path) -> None:
    package_json = json.loads((project_root / "package.json").read_text(encoding="utf-8"))
    script_text = (
        project_root / "scripts" / "collect_rc_handoff_template.ps1"
    ).read_text(encoding="utf-8")
    release_gate = (
        project_root / "docs" / "qa" / "release-gate.md"
    ).read_text(encoding="utf-8")

    assert (
        package_json["scripts"]["evidence:rc-handoff"]
        == "powershell -ExecutionPolicy Bypass -File ./scripts/collect_rc_handoff_template.ps1"
    )
    assert (
        package_json["scripts"]["evidence:rc-handoff-template"]
        == "powershell -ExecutionPolicy Bypass -File ./scripts/collect_rc_handoff_template.ps1"
    )
    assert "NOT_RELEASE_CANDIDATE_SIGNOFF" in script_text
    assert "manual_rc_handoff_required" in script_text
    assert "claim_allowed = $false" in script_text
    assert "npm run evidence:rc-handoff" in release_gate
    assert r".\scripts\collect_rc_handoff_template.ps1" in release_gate
    assert "`summary.status=manual_rc_handoff_required`" in release_gate
    assert "`release_candidate_signoff=false`" in release_gate
    assert "`claim_allowed=false`" in release_gate
    assert "does not run gates" in release_gate
    assert "must not be treated as release-candidate pass" in release_gate
