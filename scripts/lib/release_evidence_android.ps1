function New-AndroidRealDeviceTemplateLatestSummary {
    param(
        [Parameter(Mandatory = $true)]$androidRealDeviceEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

$androidRealDeviceTemplateCheckNames = @(
    "apk_installed",
    "camera_qr_pairing",
    "https_api_reachability",
    "certificate_trust_path",
    "approval_wss",
    "remote_screen_wss",
    "remote_input_wss",
    "click_input_approval",
    "text_input_approval",
    "key_pagedown_approval",
    "mobile_end_control_readonly",
    "desktop_revoke_readonly",
    "grant_expiry_readonly",
    "background_or_lockscreen_privacy",
    "artifact_redaction_review"
)
$latestAndroidRealDeviceTemplate = Find-LatestJsonArtifact $androidRealDeviceEvidenceRootPath "android-real-device-evidence.redacted.template.json"
$androidRealDeviceTemplateLatestSummary = if ($latestAndroidRealDeviceTemplate.found -and $null -ne $latestAndroidRealDeviceTemplate.data) {
    $androidTemplateMismatches = New-Object System.Collections.Generic.List[string]
    $androidTemplateCheckStatuses = [ordered]@{}

    if ([string]$latestAndroidRealDeviceTemplate.data.artifact_type -ne "android-real-device-remote-control-evidence") {
        $androidTemplateMismatches.Add("artifact_type is not android-real-device-remote-control-evidence")
    }
    if ([string]$latestAndroidRealDeviceTemplate.data.template_status -ne "manual_real_device_evidence_required") {
        $androidTemplateMismatches.Add("template_status is not manual_real_device_evidence_required")
    }
    if ([string]$latestAndroidRealDeviceTemplate.data.real_device_result -ne "uncollected") {
        $androidTemplateMismatches.Add("real_device_result is not uncollected")
    }
    if (-not (Test-JsonFalse $latestAndroidRealDeviceTemplate.data.claim_controls.real_device_pass_claim_allowed)) {
        $androidTemplateMismatches.Add("claim_controls.real_device_pass_claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestAndroidRealDeviceTemplate.data.claim_controls.binding_ref_used_for_shareable_artifacts)) {
        $androidTemplateMismatches.Add("claim_controls.binding_ref_used_for_shareable_artifacts is not false")
    }
    if (-not (Test-JsonFalse $latestAndroidRealDeviceTemplate.data.claim_controls.raw_device_grant_ids_local_only)) {
        $androidTemplateMismatches.Add("claim_controls.raw_device_grant_ids_local_only is not false")
    }
    if ([string]$latestAndroidRealDeviceTemplate.data.shareable_identity_policy.public_remote_input_correlation -notmatch "binding_ref") {
        $androidTemplateMismatches.Add("shareable_identity_policy.public_remote_input_correlation does not require binding_ref")
    }
    foreach ($checkName in $androidRealDeviceTemplateCheckNames) {
        $check = $latestAndroidRealDeviceTemplate.data.checks.$checkName
        $checkStatus = [string]$check.status
        $androidTemplateCheckStatuses[$checkName] = if ($checkStatus -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
        if ($checkStatus -ne "uncollected") {
            $androidTemplateMismatches.Add("checks.$checkName.status is not uncollected")
        }
        if ([string]::IsNullOrWhiteSpace([string]$check.required_evidence)) {
            $androidTemplateMismatches.Add("checks.$checkName.required_evidence is missing")
        }
        if ([string]::IsNullOrWhiteSpace([string]$check.overclaim_guard)) {
            $androidTemplateMismatches.Add("checks.$checkName.overclaim_guard is missing")
        }
    }
    foreach ($redactionFlag in @("tokens_absent", "pairing_codes_absent", "raw_hosts_absent", "raw_device_ids_absent", "raw_grant_ids_absent", "private_paths_absent", "binding_ref_or_redacted_active_grant_label_used")) {
        if (-not (Test-JsonFalse $latestAndroidRealDeviceTemplate.data.redaction.$redactionFlag)) {
            $androidTemplateMismatches.Add("redaction.$redactionFlag is not false in the fail-closed template")
        }
    }
    if (-not (Test-ArrayContainsText $latestAndroidRealDeviceTemplate.data.must_not_claim "real-device Android remote-control pass")) {
        $androidTemplateMismatches.Add("must_not_claim is missing real-device Android remote-control pass")
    }
    if ($androidTemplateMismatches.Count -gt 0) {
        $contractFailures.Add("latest Android real-device evidence template artifact failed fail-closed contract validation")
    }

    [ordered]@{
        found = $true
        path = $latestAndroidRealDeviceTemplate.path
        last_write_utc = $latestAndroidRealDeviceTemplate.last_write_utc
        source_contract_status = if ($androidTemplateMismatches.Count -eq 0) { "valid_fail_closed_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($androidTemplateMismatches)
        template_status = if ([string]$latestAndroidRealDeviceTemplate.data.template_status -eq "manual_real_device_evidence_required") { "manual_real_device_evidence_required" } else { "invalid_redacted" }
        real_device_result = if ([string]$latestAndroidRealDeviceTemplate.data.real_device_result -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
        review_status = if ([string]$latestAndroidRealDeviceTemplate.data.review.status -eq "unreviewed") { "unreviewed" } else { "invalid_redacted" }
        pass_claim_allowed = $false
        redaction_reviewed = $false
        evidence_artifacts_reviewed = $false
        check_statuses = $androidTemplateCheckStatuses
        build_environment = [ordered]@{
            local_apk_build_ready = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.local_apk_build_ready
            local_eas_cli_declared = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.local_eas_cli_declared
            local_eas_cli_binary_present = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.local_eas_cli_binary_present
            expo_token_present = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.expo_token_present
            eas_cloud_auth_verified = Test-JsonTrue $latestAndroidRealDeviceTemplate.data.build_environment.eas_cloud_auth_verified
            build_blocker_summary = Redact-TextValue ([string]$latestAndroidRealDeviceTemplate.data.build_environment.build_blocker_summary)
        }
        shareable_identity_policy = [ordered]@{
            public_remote_input_correlation = Redact-TextValue ([string]$latestAndroidRealDeviceTemplate.data.shareable_identity_policy.public_remote_input_correlation)
            raw_device_id_storage = Redact-TextValue ([string]$latestAndroidRealDeviceTemplate.data.shareable_identity_policy.raw_device_id_storage)
            raw_grant_id_storage = Redact-TextValue ([string]$latestAndroidRealDeviceTemplate.data.shareable_identity_policy.raw_grant_id_storage)
        }
        must_not_claim = @($latestAndroidRealDeviceTemplate.data.must_not_claim | ForEach-Object { Redact-TextValue ([string]$_) })
    }
}
elseif ($latestAndroidRealDeviceTemplate.found) {
    $contractFailures.Add("latest Android real-device evidence template artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestAndroidRealDeviceTemplate.path
        last_write_utc = $latestAndroidRealDeviceTemplate.last_write_utc
        source_contract_status = "source_contract_mismatch"
        parse_error = $latestAndroidRealDeviceTemplate.error
        template_status = "source_contract_mismatch"
        pass_claim_allowed = $false
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $androidRealDeviceEvidenceRootPath
        source_contract_status = "not_collected_by_this_packet"
        template_status = "not_collected_by_this_packet"
        real_device_result = "uncollected"
        pass_claim_allowed = $false
    }
}

    return $androidRealDeviceTemplateLatestSummary
}

function New-AndroidReleaseGateLatestSummary {
    param(
        [Parameter(Mandatory = $true)]$androidReleaseGateEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

$latestAndroidReleaseGate = Find-LatestJsonArtifact $androidReleaseGateEvidenceRootPath "android-release-gate.redacted.json"
$androidReleaseGateLatestSummary = if ($latestAndroidReleaseGate.found -and $null -ne $latestAndroidReleaseGate.data) {
    $androidGateMismatches = New-Object System.Collections.Generic.List[string]
    $androidArtifactType = [string]$latestAndroidReleaseGate.data.artifact_type
    $androidGeneratedBy = [string]$latestAndroidReleaseGate.data.generated_by
    $androidGeneratedAt = [string]$latestAndroidReleaseGate.data.generated_at_utc
    $androidStatus = [string]$latestAndroidReleaseGate.data.status
    $allowedAndroidStatuses = @("preflight_ready_not_release", "blocked", "passed")
    $androidReleaseReady = Test-JsonTrue $latestAndroidReleaseGate.data.release_ready
    $androidPreflightOnly = Test-JsonTrue $latestAndroidReleaseGate.data.preflight_only
    $installClaimAllowed = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.installable_android_app_claim_allowed
    $remoteClaimAllowed = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.real_device_remote_control_claim_allowed
    $expoPreviewIsNotRelease = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.expo_preview_is_not_release
    $requiresApkEvidence = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.requires_reviewed_apk_install_evidence
    $requiresWssEvidence = Test-JsonTrue $latestAndroidReleaseGate.data.claim_controls.requires_reviewed_https_wss_remote_control_evidence
    $artifactLabel = Redact-DisplayLabel ([string]$latestAndroidReleaseGate.data.android_artifact.label)
    $artifactProvided = Test-JsonTrue $latestAndroidReleaseGate.data.android_artifact.provided
    $artifactSha256 = [string]$latestAndroidReleaseGate.data.android_artifact.sha256
    $installableApk = Test-JsonTrue $latestAndroidReleaseGate.data.android_artifact.installable_apk
    $apkZipHeaderValid = Test-JsonTrue $latestAndroidReleaseGate.data.android_artifact.apk_zip_header_valid
    $artifactBytes = Get-StrictJsonNonNegativeIntegerOrZero $latestAndroidReleaseGate.data.android_artifact.bytes
    $artifactGateEvaluated = Test-JsonTrue $latestAndroidReleaseGate.data.artifact_gate.evaluated
    $artifactGatePassed = Test-JsonTrue $latestAndroidReleaseGate.data.artifact_gate.passed
    $realDeviceGateEvaluated = Test-JsonTrue $latestAndroidReleaseGate.data.real_device_gate.evaluated
    $realDeviceGatePassed = Test-JsonTrue $latestAndroidReleaseGate.data.real_device_gate.passed
    $sourceConfigPassed = Test-JsonTrue $latestAndroidReleaseGate.data.source_config.passed

    if ($androidArtifactType -ne "android-release-gate-summary") {
        $androidGateMismatches.Add("artifact_type is not android-release-gate-summary")
    }
    if ($androidGeneratedBy -ne "scripts/verify_android_release_gate.ps1") {
        $androidGateMismatches.Add("generated_by is not scripts/verify_android_release_gate.ps1")
    }
    if (-not (Test-UtcTimestampValue $androidGeneratedAt)) {
        $androidGateMismatches.Add("generated_at_utc is not a UTC timestamp")
    }
    if ($androidStatus -notin $allowedAndroidStatuses) {
        $androidGateMismatches.Add("status is not an allowed Android release gate status")
    }
    if ($androidStatus -eq "preflight_ready_not_release" -and -not $androidPreflightOnly) {
        $androidGateMismatches.Add("preflight_ready_not_release Android gate must set preflight_only=true")
    }
    if ($androidStatus -eq "passed" -and $androidPreflightOnly) {
        $androidGateMismatches.Add("passed Android gate must set preflight_only=false")
    }
    if ($androidPreflightOnly) {
        if ($artifactGateEvaluated) {
            $androidGateMismatches.Add("preflight Android gate must not evaluate artifact_gate")
        }
        if ($artifactGatePassed) {
            $androidGateMismatches.Add("preflight Android gate must not set artifact_gate.passed=true")
        }
        if ($realDeviceGateEvaluated) {
            $androidGateMismatches.Add("preflight Android gate must not evaluate real_device_gate")
        }
        if ($realDeviceGatePassed) {
            $androidGateMismatches.Add("preflight Android gate must not set real_device_gate.passed=true")
        }
    }
    if (-not $sourceConfigPassed) {
        $androidGateMismatches.Add("source_config.passed is not true")
    }
    if (-not $expoPreviewIsNotRelease) {
        $androidGateMismatches.Add("claim_controls.expo_preview_is_not_release is not true")
    }
    if (-not $requiresApkEvidence) {
        $androidGateMismatches.Add("claim_controls.requires_reviewed_apk_install_evidence is not true")
    }
    if (-not $requiresWssEvidence) {
        $androidGateMismatches.Add("claim_controls.requires_reviewed_https_wss_remote_control_evidence is not true")
    }
    if ($androidStatus -eq "passed") {
        if (-not $androidReleaseReady) {
            $androidGateMismatches.Add("passed Android gate must set release_ready=true")
        }
        if (-not $installClaimAllowed) {
            $androidGateMismatches.Add("passed Android gate must allow installable Android app claims")
        }
        if (-not $remoteClaimAllowed) {
            $androidGateMismatches.Add("passed Android gate must allow real-device remote-control claims")
        }
        if ($artifactSha256 -notmatch "^[a-fA-F0-9]{64}$") {
            $androidGateMismatches.Add("passed Android gate must include a 64-character android_artifact.sha256")
        }
        if ($artifactBytes -lt 1048576) {
            $androidGateMismatches.Add("passed Android gate must include an Android artifact of at least 1 MiB")
        }
        if (-not (Test-EmptyArrayValue $latestAndroidReleaseGate.data.source_config.issues)) {
            $androidGateMismatches.Add("passed Android gate must have no source_config issues")
        }
        if (-not (Test-EmptyArrayValue $latestAndroidReleaseGate.data.artifact_gate.issues)) {
            $androidGateMismatches.Add("passed Android gate must have no artifact_gate issues")
        }
        if (-not (Test-EmptyArrayValue $latestAndroidReleaseGate.data.real_device_gate.issues)) {
            $androidGateMismatches.Add("passed Android gate must have no real_device_gate issues")
        }
        if (-not ($artifactProvided -and $installableApk -and $apkZipHeaderValid -and $artifactBytes -ge 1048576 -and $artifactGateEvaluated -and $artifactGatePassed -and $realDeviceGateEvaluated -and $realDeviceGatePassed)) {
            $androidGateMismatches.Add("passed Android gate must include installable APK and real-device evidence gates")
        }
    }
    else {
        if ($androidReleaseReady) {
            $androidGateMismatches.Add("non-passed Android gate must not set release_ready=true")
        }
        if ($installClaimAllowed) {
            $androidGateMismatches.Add("non-passed Android gate must not allow installable Android app claims")
        }
        if ($remoteClaimAllowed) {
            $androidGateMismatches.Add("non-passed Android gate must not allow real-device remote-control claims")
        }
        if (-not (Test-ArrayContainsText $latestAndroidReleaseGate.data.must_not_claim "installable Android app release pass")) {
            $androidGateMismatches.Add("non-passed Android gate must include installable Android app release pass in must_not_claim")
        }
        if (-not (Test-ArrayContainsText $latestAndroidReleaseGate.data.must_not_claim "real-device Android remote-control pass")) {
            $androidGateMismatches.Add("non-passed Android gate must include real-device Android remote-control pass in must_not_claim")
        }
    }

    if ($androidGateMismatches.Count -gt 0) {
        $contractFailures.Add("latest Android release gate artifact failed redacted contract validation")
    }

    $androidGateSourceContractValid = $androidGateMismatches.Count -eq 0

    [ordered]@{
        found = $true
        path = $latestAndroidReleaseGate.path
        last_write_utc = $latestAndroidReleaseGate.last_write_utc
        source_contract_status = if ($androidGateSourceContractValid) { "valid_redacted_summary" } else { "source_contract_mismatch" }
        mismatch_reasons = @($androidGateMismatches)
        status = if ($androidStatus -in $allowedAndroidStatuses -and $androidGateSourceContractValid) { $androidStatus } elseif ($androidStatus -in $allowedAndroidStatuses) { "source_contract_mismatch" } else { "invalid_redacted" }
        release_ready = if ($androidGateSourceContractValid) { $androidReleaseReady } else { $false }
        preflight_only = if ($androidGateSourceContractValid) { $androidPreflightOnly } else { $false }
        source_config_passed = if ($androidGateSourceContractValid) { $sourceConfigPassed } else { $false }
        android_artifact = [ordered]@{
            provided = if ($androidGateSourceContractValid) { $artifactProvided } else { $false }
            label = $artifactLabel
            bytes = if ($androidGateSourceContractValid) { $artifactBytes } else { 0 }
            installable_apk = if ($androidGateSourceContractValid) { $installableApk } else { $false }
            apk_zip_header_valid = if ($androidGateSourceContractValid) { $apkZipHeaderValid } else { $false }
        }
        artifact_gate_evaluated = if ($androidGateSourceContractValid) { $artifactGateEvaluated } else { $false }
        artifact_gate_passed = if ($androidGateSourceContractValid) { $artifactGatePassed } else { $false }
        real_device_gate_evaluated = if ($androidGateSourceContractValid) { $realDeviceGateEvaluated } else { $false }
        real_device_gate_passed = if ($androidGateSourceContractValid) { $realDeviceGatePassed } else { $false }
        real_device_evidence_label = Redact-DisplayLabel ([string]$latestAndroidReleaseGate.data.real_device_gate.evidence_label)
        claim_controls = [ordered]@{
            installable_android_app_claim_allowed = if ($androidGateSourceContractValid) { $installClaimAllowed } else { $false }
            real_device_remote_control_claim_allowed = if ($androidGateSourceContractValid) { $remoteClaimAllowed } else { $false }
            expo_preview_is_not_release = if ($androidGateSourceContractValid) { $expoPreviewIsNotRelease } else { $false }
            requires_reviewed_apk_install_evidence = if ($androidGateSourceContractValid) { $requiresApkEvidence } else { $false }
            requires_reviewed_https_wss_remote_control_evidence = if ($androidGateSourceContractValid) { $requiresWssEvidence } else { $false }
        }
        warnings_count = Get-ArrayCount $latestAndroidReleaseGate.data.warnings
        must_not_claim = @($latestAndroidReleaseGate.data.must_not_claim | ForEach-Object { Redact-TextValue ([string]$_) })
    }
}
elseif ($latestAndroidReleaseGate.found) {
    $contractFailures.Add("latest Android release gate artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestAndroidReleaseGate.path
        last_write_utc = $latestAndroidReleaseGate.last_write_utc
        source_contract_status = "source_contract_mismatch"
        parse_error = $latestAndroidReleaseGate.error
        status = "source_contract_mismatch"
        release_ready = $false
        claim_controls = [ordered]@{
            installable_android_app_claim_allowed = $false
            real_device_remote_control_claim_allowed = $false
        }
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $androidReleaseGateEvidenceRootPath
        source_contract_status = "not_collected_by_this_packet"
        status = "not_collected_by_this_packet"
        release_ready = $false
        claim_controls = [ordered]@{
            installable_android_app_claim_allowed = $false
            real_device_remote_control_claim_allowed = $false
        }
    }
}

    return $androidReleaseGateLatestSummary
}
