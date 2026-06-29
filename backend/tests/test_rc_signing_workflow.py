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
    assert "sign:windows:preflight" in text
    assert "windows_signing_preflight.ps1" in text
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
    helper = (REPO_ROOT / "scripts" / "sign_windows_trusted_signing.ps1").read_text(encoding="utf-8")
    assert "sign_windows_trusted_signing.ps1" in backend
    assert "sign_windows_trusted_signing.ps1" in portable
    assert "Invoke-TrustedWindowsSigning" in backend
    assert "Invoke-TrustedWindowsSigning" in portable
    assert "[switch]$AllowModuleInstall" in backend
    assert "[switch]$AllowModuleInstall" in portable
    assert "-AllowModuleInstall:$AllowModuleInstall" in backend
    assert "-AllowModuleInstall:$AllowModuleInstall" in portable
    assert '$requiredVersion = "0.5.0"' in helper
    assert "Install-Module -Name TrustedSigning -RequiredVersion $requiredVersion" in helper
    assert "Where-Object { $_.Version.ToString() -eq $requiredVersion }" in helper
    assert "TrustedSigning module online install is disabled by default" in helper
    assert "pass -AllowModuleInstall only on a controlled runner" in helper


def test_windows_signing_preflight_is_metadata_only() -> None:
    text = (REPO_ROOT / "scripts" / "windows_signing_preflight.ps1").read_text(encoding="utf-8")
    assert "windows-signing-preflight.json" in text
    assert "contains_secret_values = $false" in text
    assert "signs_files = $false" in text
    assert "release_signoff = $false" in text
    assert "contains_secret_values = $false" in text
    assert "ready_to_attempt_any_signing" in text
    assert "ready_to_attempt_pfx_signing" in text
    assert "missing_pfx_env" in text
    assert "AZURE_TRUSTED_SIGNING_CERTIFICATE_THUMBPRINT" in text
    assert "signer_subject" in text
    assert "signer_thumbprint" in text
    assert "timestamp_subject" in text
    assert "publisher_mismatch_artifacts" in text
    assert "thumbprint_mismatch_artifacts" in text
    assert "missing_timestamp_artifacts" in text
    assert "blockers" in text
    assert "if ($blockers.Count -gt 0)" in text
    assert "next_actions" in text
    assert "Get-AuthenticodeSignature" in text
    assert "check_unavailable" in text
    assert "Invoke-TrustedSigning" not in text
    assert "Set-AuthenticodeSignature" not in text
