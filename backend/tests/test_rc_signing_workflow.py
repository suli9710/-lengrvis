"""Contract tests for Windows RC signing workflow wiring."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RC_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-candidate.yml"
RELEASE_PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-publish.yml"
REVIEWED_EVIDENCE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-reviewed-evidence.yml"
PACKAGE_JSON = REPO_ROOT / "package.json"
CURRENT_EVIDENCE_SCRIPT = REPO_ROOT / "scripts" / "generate_current_release_evidence.ps1"


def test_release_candidate_workflow_orders_windows_signing_steps() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")
    backend_idx = text.index("sign_windows_backend.ps1")
    launcher_idx = text.index("-LauncherOnly")
    refresh_idx = text.index("refresh_portable_release_bundle.ps1")
    sfx_idx = text.index("-SelfExtractingOnly")
    dist_idx = text.index("dist:signed")
    assert backend_idx < launcher_idx < refresh_idx < sfx_idx < dist_idx


def test_release_candidate_workflow_splits_success_and_failure_artifacts() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")
    success_section = text[
        text.index("Upload release candidate artifacts") : text.index("Upload blocked release candidate diagnostics")
    ]
    failure_section = text[text.index("Upload blocked release candidate diagnostics") :]

    assert "if: success()" in success_section
    assert "build/delivery-candidate-verdict.json" in success_section
    assert "dist/backend.exe" in success_section
    assert "dist/Lengrvis-win-portable/**" in success_section
    assert "dist/Lengrvis-win-portable.zip" in success_section
    assert "dist/Lengrvis-*-x64-self-extracting.exe" in success_section
    assert "desktop/release/**" in success_section
    assert ".tmp/sbom/lengrvis-sbom.cdx.json" in success_section
    assert "include-hidden-files: true" in success_section
    assert "build/release-candidate-subjects.sha256" in success_section
    assert "build/attestations/**" in success_section
    assert "build/*-evidence-reviewed.json" not in success_section
    assert "build/android-real-device-evidence-reviewed.json" not in success_section
    assert "if: failure()" in failure_section
    assert "include-hidden-files: true" in failure_section
    assert "docs/release/current-release-evidence.md" in failure_section
    assert "desktop/release" not in failure_section


def test_release_candidate_workflow_attests_provenance_and_sbom() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")
    prepare_index = text.index("Prepare release candidate attestation subjects")
    provenance_index = text.index("Attest release candidate build provenance")
    sbom_index = text.index("Attest release candidate SBOM")
    preserve_index = text.index("Preserve release candidate attestation bundles")
    upload_index = text.index("Upload release candidate artifacts")

    assert "id-token: write" in text
    assert "attestations: write" in text
    assert "artifact-metadata: write" in text
    assert text.count("actions/attest@a1948c3f048ba23858d222213b7c278aabede763 # v4.1.1") == 2
    assert "subject-checksums: build/release-candidate-subjects.sha256" in text
    assert "sbom-path: .tmp/sbom/lengrvis-sbom.cdx.json" in text
    assert '$sbom.bomFormat -ne "CycloneDX"' in text
    assert "Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256" in text
    assert "build-provenance.sigstore.json" in text
    assert "sbom.sigstore.json" in text
    assert (
        text.index("Automated candidate delivery verdict")
        < prepare_index
        < provenance_index
        < sbom_index
        < preserve_index
        < upload_index
    )


def test_release_candidate_workflow_runs_delivery_rc_once() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")

    assert text.count("npm run delivery:candidate") == 1
    assert "npm run delivery:rc" not in text
    assert "npm run release:check" not in text


def test_release_candidate_uploads_automated_candidate_without_requiring_reviewed_evidence() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")
    verdict = text.index("Automated candidate delivery verdict")
    upload = text.index("Upload release candidate artifacts")

    assert "npm run delivery:candidate" in text
    assert "npm run delivery:rc" not in text
    assert verdict < upload
    assert "build/*-evidence-reviewed.json" not in text


def test_release_publish_promotes_downloaded_candidate_bytes_after_strict_verification() -> None:
    text = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "reviewed_evidence_run_id" in text
    assert 'gh run download "$env:LENGRVIS_RELEASE_CANDIDATE_RUN_ID"' in text
    assert 'gh run download "$env:LENGRVIS_REVIEWED_EVIDENCE_RUN_ID"' in text
    assert "release-candidate-artifacts" in text
    assert "release-reviewed-evidence" in text
    assert "build_all.ps1" not in text
    assert "dist:publish" not in text
    assert "electron-builder --publish" not in text
    assert text.index("Download immutable candidate artifacts") < text.index("npm run delivery:rc")
    assert text.index("npm run delivery:rc") < text.index("Create or update draft GitHub Release")
    assert text.index("verify:windows-release-signatures") < text.index("Create or update draft GitHub Release")
    assert "desktop/release/*.exe" in text


def test_release_publish_verifies_candidate_manifest_and_github_provenance_before_materializing() -> None:
    text = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    download_index = text.index("Download immutable candidate artifacts into isolated staging")
    verify_index = text.index("Verify immutable candidate checksums and provenance")
    materialize_index = text.index("Materialize only allowed staged release inputs")
    verify_section = text[verify_index:materialize_index]

    assert "attestations: read" in text
    assert download_index < verify_index < materialize_index
    assert "build/release-candidate-subjects.sha256" in verify_section
    assert "build/attestations/build-provenance.sigstore.json" in verify_section
    assert "build/attestations/sbom.sigstore.json" in verify_section
    assert "Candidate checksum manifest contains an invalid line" in verify_section
    assert 'StartsWith("dist/"' in verify_section
    assert 'StartsWith("desktop/release/"' in verify_section
    assert "Candidate checksum manifest path escapes the immutable candidate root" in verify_section
    assert "Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256" in verify_section
    assert verify_section.count('gh attestation verify "$artifactPath"') == 2
    assert '--bundle "$provenanceBundle"' in verify_section
    assert '--predicate-type "https://slsa.dev/provenance/v1"' in verify_section
    assert '--bundle "$sbomBundle"' in verify_section
    assert '--predicate-type "https://cyclonedx.org/bom"' in verify_section
    assert '--repo "$env:GITHUB_REPOSITORY"' in verify_section
    assert '--signer-workflow "$env:GITHUB_REPOSITORY/.github/workflows/release-candidate.yml"' in verify_section
    assert '--source-digest "$env:LENGRVIS_RELEASE_CANDIDATE_COMMIT"' in verify_section
    assert "--deny-self-hosted-runners" in verify_section
    assert verify_section.count("$LASTEXITCODE -ne 0") >= 2
    assert "GitHub provenance verification failed" in verify_section
    assert "GitHub SBOM attestation verification failed" in verify_section
    assert "$sbomBundleDocument.dsseEnvelope.payload" in verify_section
    assert '$statement.predicateType -ne "https://cyclonedx.org/bom"' in verify_section
    assert "$signedSbom -cne $downloadedSbom" in verify_section


def test_release_publish_materializes_complete_strict_inputs_without_literal_wildcards() -> None:
    text = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    section = text[
        text.index("Materialize only allowed staged release inputs") : text.index("Set up Node.js")
    ]

    assert 'Copy-Item -LiteralPath (Join-Path $source "*")' not in section
    assert 'Get-ChildItem -LiteralPath $source -Force | Copy-Item' in section
    assert "$candidateRoot" in section
    for relative_path in (
        "build/delivery-candidate-verdict.json",
        ".tmp/sbom/lengrvis-sbom.cdx.json",
        "build/distribution-release-evidence-reviewed.json",
        "build/clean-machine-release-evidence-reviewed.json",
        "build/result-quality-review-evidence-reviewed.json",
        "build/diagnostics-external-review-evidence-reviewed.json",
        "build/android-real-device-evidence-reviewed.json",
        "build/android/lengrvis-production.apk",
    ):
        assert relative_path in section


def test_release_publish_includes_verified_sbom_as_a_release_asset() -> None:
    text = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    prepare_section = text[
        text.index("Prepare verified GitHub Release assets and checksums") : text.index(
            "Create or update draft GitHub Release"
        )
    ]

    assert '$sbomSource = ".tmp/sbom/lengrvis-sbom.cdx.json"' in prepare_section
    assert '$sbomTarget = "dist/Lengrvis-$version-sbom.cdx.json"' in prepare_section
    assert "Copy-Item -LiteralPath $sbomSource -Destination $sbomTarget" in prepare_section
    assert "$uploadAssets += (Resolve-Path -LiteralPath $sbomTarget).Path" in prepare_section


def test_upload_artifact_steps_preserve_explicit_dot_directory_evidence() -> None:
    for workflow_path in (CI_WORKFLOW, RC_WORKFLOW, RELEASE_PUBLISH_WORKFLOW):
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                uses = str(step.get("uses") or "")
                if not uses.startswith("actions/upload-artifact@"):
                    continue
                inputs = step.get("with") or {}
                if ".tmp/" not in str(inputs.get("path") or ""):
                    continue
                assert inputs.get("include-hidden-files") is True, (
                    f"{workflow_path.name}: {step.get('name')} drops dot-directory evidence"
                )


def test_reviewed_evidence_workflow_produces_the_publish_contract() -> None:
    assert REVIEWED_EVIDENCE_WORKFLOW.exists()
    text = REVIEWED_EVIDENCE_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "environment: production" in text
    assert "release-reviewed-evidence" in text
    assert "actions/upload-artifact@" in text
    assert "if-no-files-found: error" in text


def test_release_publish_reads_reviewed_artifact_paths_relative_to_upload_common_root() -> None:
    reviewed = REVIEWED_EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
    publish = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    upload_section = reviewed[reviewed.index("Upload immutable reviewed release evidence") :]
    materialize_section = publish[
        publish.index("Materialize only allowed staged release inputs") : publish.index("Set up Node.js")
    ]

    # upload-artifact strips the least common ancestor (build/) from these
    # paths.  The promotion workflow must therefore address files from the
    # downloaded artifact root and explicitly map them back under build/.
    assert "build/distribution-release-evidence-reviewed.json" in upload_section
    assert '"distribution-release-evidence-reviewed.json" = "build/distribution-release-evidence-reviewed.json"' in (
        materialize_section
    )
    assert '"android/lengrvis-production.apk" = "build/android/lengrvis-production.apk"' in materialize_section
    assert 'Join-Path $evidenceRoot $relative' not in materialize_section


def test_reviewed_evidence_workflow_validates_candidate_and_human_inputs_before_upload() -> None:
    text = REVIEWED_EVIDENCE_WORKFLOW.read_text(encoding="utf-8")
    upload_index = text.index("Upload immutable reviewed release evidence")

    for marker in (
        "release-candidate.yml",
        "candidateRun.workflow_id",
        "candidateRun.run_attempt",
        "candidateRun.event",
        "candidateRun.head_repository.full_name",
        "candidateRun.head_branch",
        "candidateRun.head_sha",
        "$expectedReviewBundleTag",
        "package.json",
        "release-candidate-artifacts",
        "review_bundle_tag",
        "reviewRelease.draft",
        "reviewRelease.target_commitish",
        "verify_distribution_release_evidence.py --require-candidate-binding",
        "verify_clean_machine_evidence.py --require-candidate-binding",
        "verify_result_quality_reviewed_evidence.py --require-candidate-binding",
        "verify_diagnostics_external_reviewed_evidence.py --require-candidate-binding",
        "verify_android_reviewed_evidence.py --require-candidate-binding",
        "verify_android_release_gate.ps1",
    ):
        assert marker in text
        assert text.index(marker) < upload_index


def test_release_workflows_reject_non_default_branch_dispatches_before_protected_environments() -> None:
    default_branch_guard = "if: github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"

    for workflow in (RC_WORKFLOW, REVIEWED_EVIDENCE_WORKFLOW, RELEASE_PUBLISH_WORKFLOW):
        text = workflow.read_text(encoding="utf-8")
        job_section = text[text.index("jobs:") :]
        assert default_branch_guard in job_section
        assert job_section.index(default_branch_guard) < job_section.index("environment:")


def test_release_candidate_workflow_runs_review_scorecard_before_build() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")

    assert "npm run review:scorecard" in text
    assert text.index("Install Playwright Chromium") < text.index("npm run review:scorecard")
    assert text.index("npm run review:scorecard") < text.index("Build release artifacts")


def test_release_publish_workflow_runs_review_scorecard_before_build() -> None:
    text = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "npm run review:scorecard" in text
    assert text.index("Verify release tag matches desktop version") < text.index("npm run review:scorecard")
    assert text.index("npm run review:scorecard") < text.index("Verify downloaded candidate signatures")
    assert "Build release artifacts" not in text


def test_release_publish_workflow_fails_closed_before_creating_a_release() -> None:
    text = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "push:\n    tags:" not in text
    assert 'LENGRVIS_STRICT_STATE_MACHINE: "true"' in text
    assert 'LENGRVIS_ALLOW_MOCK_FALLBACK: "false"' in text
    assert "npm run delivery:rc" in text
    assert text.index("npm run delivery:rc") < text.index("Create or update draft GitHub Release")
    assert "Verify downloaded candidate signatures" in text
    assert "Download immutable candidate artifacts" in text
    assert "unsigned portable/backend assets" not in text
    for step in ("hygiene", "qa_gate", "real_llm_quality", "supply_chain", "extension_security"):
        assert f"${{{{ steps.{step}.outcome }}}}" in text


def test_release_workflows_bind_strict_evidence_to_the_checked_out_candidate() -> None:
    rc = RC_WORKFLOW.read_text(encoding="utf-8")
    publish = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    for text in (rc, publish):
        assert "LENGRVIS_RELEASE_CANDIDATE_COMMIT" in text
        assert "LENGRVIS_RELEASE_BUILD_IDENTIFIER" in text
        assert "LENGRVIS_RELEASE_CANDIDATE_REPOSITORY" in text
        assert "LENGRVIS_RELEASE_CANDIDATE_RUN_ID" in text
        assert "LENGRVIS_RELEASE_CANDIDATE_RUN_ATTEMPT" in text
        assert "verify_release_candidate_binding.py" in text
        assert "--require-checkout-match" in text

    assert "candidate_run_id" in publish
    assert "candidate_run_attempt" in publish
    assert "git rev-parse HEAD" in publish


def test_release_publish_verifies_server_side_candidate_identity_before_checkout() -> None:
    text = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    validation_index = text.index("Validate immutable release candidate before checkout")
    checkout_index = text.index("Checkout verified release candidate commit")
    tag_binding_index = text.index("Verify supplied release tag is bound to verified candidate")

    assert "actions: read" in text
    assert validation_index < checkout_index < tag_binding_index
    assert "id: candidate_identity" in text
    assert "repos/$env:GITHUB_REPOSITORY/actions/workflows/release-candidate.yml" in text
    assert "repos/$env:GITHUB_REPOSITORY/actions/runs/$candidateRunId" in text
    assert "candidateRun.workflow_id" in text
    assert "candidateRun.run_attempt" in text
    assert "candidateRun.conclusion" in text
    assert "candidateRun.head_repository.full_name" in text
    assert "candidateRun.head_branch" in text
    assert "$env:GITHUB_REF_NAME -ne [string]$repository.default_branch" in text
    assert "refs/tags/$env:RELEASE_TAG" in text
    assert "ref: ${{ steps.candidate_identity.outputs.commit }}" in text
    assert "ref: ${{ env.RELEASE_TAG }}" not in text


def test_release_publish_accepts_reviewed_evidence_only_from_the_bound_workflow_run() -> None:
    text = RELEASE_PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    for marker in (
        "actions/workflows/release-reviewed-evidence.yml",
        "evidenceRun.workflow_id",
        "evidenceRun.event",
        "evidenceRun.head_repository.full_name",
        "evidenceRun.head_branch",
        "evidenceRun.head_sha",
    ):
        assert marker in text
    assert "evidenceRun.head_sha" in text[text.index("$evidenceRunJson") : text.index("$buildIdentifier")]


def test_release_candidate_workflow_does_not_claim_manual_review_evidence() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")
    verdict_section = text[
        text.index("Automated candidate delivery verdict") : text.index("Upload release candidate artifacts")
    ]

    assert "release_owner_signature" not in text
    assert "manual_signoff_status" not in text
    assert "RELEASE_OWNER_SIGNATURE" not in verdict_section
    assert "RELEASE_EVIDENCE_MANUAL_SIGNOFF_STATUS" not in verdict_section
    assert "RELEASE_EVIDENCE_NEEDS_JSON" not in verdict_section
    assert "npm run delivery:candidate" in verdict_section


def test_release_candidate_workflow_passes_real_llm_env_to_strict_delivery() -> None:
    text = RC_WORKFLOW.read_text(encoding="utf-8")
    verdict_section = text[
        text.index("Automated candidate delivery verdict") : text.index("Upload release candidate artifacts")
    ]

    assert "LENGRVIS_API_KEY: ${{ secrets.LENGRVIS_REAL_LLM_API_KEY }}" in verdict_section
    assert "LENGRVIS_PROVIDER_NAME: ${{ vars.LENGRVIS_REAL_LLM_PROVIDER_NAME || 'openai_compatible' }}" in verdict_section
    assert "LENGRVIS_BASE_URL: ${{ vars.LENGRVIS_REAL_LLM_BASE_URL || 'https://api.openai.com/v1' }}" in verdict_section
    assert "LENGRVIS_MODEL: ${{ vars.LENGRVIS_REAL_LLM_MODEL || 'gpt-4o-mini' }}" in verdict_section
    assert 'LENGRVIS_ALLOW_MOCK_FALLBACK: "false"' in text
    assert "LENGRVIS_MODE: efficiency" in verdict_section


def test_current_release_evidence_script_has_strict_signoff_gate() -> None:
    text = CURRENT_EVIDENCE_SCRIPT.read_text(encoding="utf-8")

    assert "[switch]$StrictReleaseSignoff" in text
    assert "ciStatus -ne \"machine_gates_passed\"" in text
    assert "PENDING_RELEASE_OWNER_SIGNATURE" in text
    assert "RELEASE_EVIDENCE_MANUAL_SIGNOFF_STATUS" in text
    assert "rc_signoff_recorded" in text
    assert "LENGRVIS_RELEASE_CANDIDATE_COMMIT" in text
    assert "LENGRVIS_RELEASE_BUILD_IDENTIFIER" in text


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
