from __future__ import annotations

from pathlib import Path


def _text(project_root: Path, relative: str) -> str:
    return (project_root / relative).read_text(encoding="utf-8")


def test_mobile_gate_commands_stay_in_ci_evidence_and_local_runner(project_root: Path) -> None:
    ci = _text(project_root, ".github/workflows/ci.yml")
    evidence = _text(project_root, "scripts/generate_current_release_evidence.ps1")
    current_evidence = _text(project_root, "docs/release/current-release-evidence.md")
    local_runner = _text(project_root, "scripts/run_tests.ps1")
    required_markers = (
        "npm exec expo -- install --check",
        "npm --prefix mobile run smoke:approval-status-label",
        "npm --prefix mobile run smoke:android-hardening-plugin",
        "npm --prefix mobile run smoke:android-lan-tls",
        ":app:assembleDebug :app:assembleDebugAndroidTest --no-daemon --stacktrace",
    )

    for marker in required_markers:
        assert marker in ci
        assert marker in evidence
        assert marker in current_evidence
        assert marker in local_runner
