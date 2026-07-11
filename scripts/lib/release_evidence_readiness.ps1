function New-ReleaseReadinessBlockers {
    param(
        [Parameter(Mandatory = $true)]$androidReleaseGateLatestSummary,
        [Parameter(Mandatory = $true)]$diagnosticsReviewLatestSummary
    )

$releaseReadinessBlockers = @(
    [ordered]@{
        id = "agentic_threat_model"
        status = "candidate_bound_review_required"
        claim_allowed = $false
        support_evidence = "versioned threat model, OWASP ASI01-ASI10 control map, validator, and release-pipeline wiring only"
        required_evidence = "passing npm run security:threat-model output bound to the candidate plus security/release-owner review of controls, evidence owners, and residual risk"
        beginner_next_step = "Run npm run security:threat-model for the candidate, review open residual risks, and record owner acceptance before public Beta or RC sign-off."
        must_not_claim = "public Beta threat-model gate passed"
    }
    [ordered]@{
        id = "clean_machine_local_model"
        status = "missing_manual_evidence"
        claim_allowed = $false
        support_evidence = "local-model template, Settings smoke, and Ollama contract counts only"
        required_evidence = "candidate artifact/build/profile plus clean-machine install/start/pull/task-smoke outcome, runtime/model/version, or exact blocked reason"
        beginner_next_step = "Run npm run evidence:local-model-template with the candidate artifact/build/profile and reviewed install/start/pull/task-smoke notes."
        must_not_claim = "clean-machine local/offline model readiness"
    }
    [ordered]@{
        id = "mobile_real_device_lan_wss"
        status = "missing_real_device_artifacts"
        claim_allowed = $false
        support_evidence = "mobile LAN/WSS preflight, backend authorization tests, and mobile client smokes only"
        required_evidence = "real phone/emulator camera QR, actual HTTPS/WSS approval/remote screen/remote input session, certificate trust path, revoke/expiry screenshots or notes"
        beginner_next_step = "Run npm run evidence:mobile-lan-wss first, then attach reviewed phone/emulator artifacts to the generated checklist."
        must_not_claim = "real-device mobile LAN/WSS pass"
    }
    [ordered]@{
        id = "android_installable_remote_control"
        status = if ($androidReleaseGateLatestSummary.release_ready) { "recorded_by_android_release_gate" } else { "missing_apk_or_real_device_gate" }
        claim_allowed = [bool]$androidReleaseGateLatestSummary.claim_controls.installable_android_app_claim_allowed -and [bool]$androidReleaseGateLatestSummary.claim_controls.real_device_remote_control_claim_allowed
        support_evidence = "Android real-device fail-closed template, Android release gate preflight, EAS profile config, and mobile client smokes only"
        required_evidence = "installable QA APK path/hash plus reviewed Android/emulator HTTPS/WSS remote-control evidence JSON backed by QR, trust, screen, input, revoke, expiry, and redaction artifacts"
        beginner_next_step = "Run npm run evidence:android-real-device-template, run npm run android:release-gate -- -PreflightOnly, build the preview APK, then rerun the strict gate with -ArtifactPath and -RealDeviceEvidencePath."
        must_not_claim = "installable Android app or real-device Android remote-control pass"
    }
    [ordered]@{
        id = "natural_language_result_quality"
        status = "missing_result_quality_signoff"
        claim_allowed = $false
        support_evidence = "portable command-dock submission plus explain completion_evidence/result_quality fields only"
        required_evidence = "reviewed user-visible result, source/artifact check, next-step/actionability check, and explicit result-quality sign-off"
        beginner_next_step = "Use the portable smoke result as routing evidence, then manually review the visible task result before claiming quality."
        must_not_claim = "natural-language result-quality sign-off"
    }
    [ordered]@{
        id = "diagnostics_external_public_safety"
        status = if ($diagnosticsReviewLatestSummary.manual_content_review_only_remaining) { "manual_content_review_only_remaining" } else { "manual_content_review_required" }
        claim_allowed = $false
        support_evidence = if ($diagnosticsReviewLatestSummary.manual_content_review_only_remaining) {
            "diagnostics export contract tests, external-review template, reviewed-evidence verifier, release packet summary, and strict diagnostics-evidence pipeline stage"
        } else {
            "diagnostics export contract tests and external-review packet template only"
        }
        required_evidence = "human review of the actual exported diagnostics package contents before any external sharing"
        beginner_next_step = "Create the signed diagnostics-external-review-evidence-reviewed JSON from the actual package content review, then run npm run evidence:diagnostics-verify."
        must_not_claim = "public-safe diagnostics approval"
    }
    [ordered]@{
        id = "release_candidate_handoff"
        status = "manual_rc_handoff_required"
        claim_allowed = $false
        support_evidence = "redacted evidence index only"
        required_evidence = "candidate commit/build id, platform/artifact labels, exact gate commands and exits, manual P1 checks, waivers, residual risks"
        beginner_next_step = "Fill rc_handoff_requirements in a separate release handoff before tagging, publishing, or announcing an RC."
        must_not_claim = "release-candidate pass"
    }
)

    return $releaseReadinessBlockers
}

