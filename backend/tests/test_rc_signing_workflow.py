"""Contract tests for Windows RC signing workflow wiring."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RC_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-candidate.yml"
PACKAGE_JSON = REPO_ROOT / "package.json"


def test_release_candidate_workflow_orders_windows_signing_steps() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")
    backend_idx = text.index("sign_windows_backend.ps1")
    launcher_idx = text.index("-LauncherOnly")
    refresh_idx = text.index("refresh_portable_release_bundle.ps1")
    sfx_idx = text.index("-SelfExtractingOnly")
    dist_idx = text.index("dist:signed")
    assert backend_idx < launcher_idx < refresh_idx < sfx_idx < dist_idx


def test_package_json_exposes_portable_signing_scripts() -> None:
    text = PACKAGE_JSON.read_text(encoding="utf-8")
    assert "refresh:portable-bundle" in text
    assert "sign:portable:launcher" in text
    assert "sign:portable:sfx" in text
    assert "sign:portable:windows" in text
    assert "sign:windows:release" in text
    assert "refresh_portable_release_bundle.ps1" in text
    assert "sign_windows_portable_artifacts.ps1" in text
    assert "-LauncherOnly" in text
    assert "-SelfExtractingOnly" in text


def test_trusted_signing_helper_is_shared_by_backend_and_portable_scripts() -> None:
    backend = (REPO_ROOT / "scripts" / "sign_windows_backend.ps1").read_text(encoding="utf-8")
    portable = (REPO_ROOT / "scripts" / "sign_windows_portable_artifacts.ps1").read_text(encoding="utf-8")
    assert "sign_windows_trusted_signing.ps1" in backend
    assert "sign_windows_trusted_signing.ps1" in portable
    assert "Invoke-TrustedWindowsSigning" in backend
    assert "Invoke-TrustedWindowsSigning" in portable
