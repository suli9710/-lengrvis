function Get-ReleaseEvidenceSourceContracts {
    param(
        [Parameter(Mandatory = $true)]$contractFailures
    )

$mobileNeedles = @(
    "evidence-summary.redacted.json",
    "redacted_evidence_summary_path",
    "real_device_evidence_status",
    "uncollected_fail_closed",
    "real_device_evidence_collected",
    "no_phone_preflight_claim",
    "not_real_device_pass",
    "ready_for_manual_real_device_collection_only",
    "approval_wss_evidence",
    "remote_screen_wss_evidence",
    "remote_input_wss_evidence",
    "Token-bearing mobile LAN flows require HTTPS and WSS",
    "This preflight does not use a phone, emulator, camera, QR scanner, or real WSS connection",
    "must not be recorded as real-device pass evidence"
)
$mobileContract = Get-SourceContract "scripts/verify_mobile_lan_wss_preflight.ps1" $mobileNeedles
if (-not $mobileContract.required_markers_present) {
    $contractFailures.Add("mobile LAN/WSS preflight source contract is missing required redaction or non-evidence markers")
}

$androidReleaseGateNeedles = @(
    "android-release-gate.redacted.json",
    "preflight_ready_not_release",
    "Strict Android release gate requires -ArtifactPath",
    "Strict Android release gate requires -RealDeviceEvidencePath",
    "artifact_type=android-real-device-remote-control-evidence",
    "artifact_not_apk_zip",
    "installable_android_app_claim_allowed",
    "real_device_remote_control_claim_allowed",
    "expo_preview_is_not_release",
    "requires_reviewed_apk_install_evidence",
    "requires_reviewed_https_wss_remote_control_evidence",
    "This is not an installable APK pass or real-device remote-control pass."
)
$androidReleaseGateContract = Get-SourceContract "scripts/verify_android_release_gate.ps1" $androidReleaseGateNeedles
if (-not $androidReleaseGateContract.required_markers_present) {
    $contractFailures.Add("Android release gate source contract is missing required fail-closed markers")
}

$androidRealDeviceTemplateNeedles = @(
    "android-real-device-evidence.redacted.template.json",
    "manual_real_device_evidence_required",
    "real_device_pass_claim_allowed",
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
    "build_environment",
    "local_apk_build_ready",
    "binding_ref",
    "raw_device_ids_absent",
    "raw_grant_ids_absent"
)
$androidRealDeviceTemplateContract = Get-SourceContract "scripts/collect_android_real_device_evidence_template.ps1" $androidRealDeviceTemplateNeedles
if (-not $androidRealDeviceTemplateContract.required_markers_present) {
    $contractFailures.Add("Android real-device evidence template source contract is missing required scenario or redaction markers")
}

$portableNeedles = @(
    "portable.status.log",
    "portable renderer DOM read-only task evidence passed",
    "portable renderer DOM natural-language read-only task evidence passed",
    "it is not accepted as natural-language task evidence",
    "This is submission/task-evidence coverage",
    "completed task-result sign-off",
    "Visible safe-failure copy is still useful safety evidence",
    "Any forbidden mutation or diagnostics export during this attempt fails the smoke"
)
$portableContract = Get-SourceContract "docs/qa/release-gate.md" $portableNeedles
if (-not $portableContract.required_markers_present) {
    $contractFailures.Add("portable first-screen smoke source contract is missing required non-signoff markers")
}

$mobileRemoteInputUiNeedles = @(
    "ApprovalActiveGrantContext",
    "REMOTE_INPUT_ACTIVE_GRANT_REASON",
    "remoteInputApprovalMatchesActiveGrant",
    "source_device_id",
    "source_grant_id",
    "required_mobile_scopes",
    "binding_ref"
)
$mobileRemoteInputClientNeedles = @(
    "assertRemoteInputApprovalMatchesSession",
    "assertRemoteInputApprovalRejectAllowedForSession",
    "Remote input approval does not match this mobile device.",
    "Remote input approval does not match the active mobile grant.",
    "getApprovalDetail(session, approvalId)",
    "remote_input_binding",
    "binding_ref",
    "allowed_device_ids",
    "claimRemoteInputGrantToken(session, explicitGrantId)"
)
$mobileRemoteInputSmokeNeedles = @(
    "remoteInputNoActiveGrant",
    "remoteInputWrongActiveGrant",
    "binding_ref",
    "matching approval details",
    "client-side remote-input binding failures must not reach the smoke server",
    "remote-input approval without a cached grant token must fail closed",
    "Approval stream connected snapshot must restore active remote-input grants after missed events"
)
$mobileRemoteInputContracts = @(
    Get-SourceContract "mobile/src/approvalSafetyDisplay.ts" $mobileRemoteInputUiNeedles
    Get-SourceContract "mobile/src/api/client/endpoints.ts" $mobileRemoteInputClientNeedles
    Get-SourceContract "mobile/scripts/remote-input-grant-smoke.cjs" $mobileRemoteInputSmokeNeedles
)
foreach ($contract in $mobileRemoteInputContracts) {
    if (-not $contract.required_markers_present) {
        $contractFailures.Add("mobile remote-input active grant source contract is missing required fail-closed markers")
        break
    }
}

    return [ordered]@{
        mobileContract = $mobileContract
        androidReleaseGateContract = $androidReleaseGateContract
        androidRealDeviceTemplateContract = $androidRealDeviceTemplateContract
        portableContract = $portableContract
        mobileRemoteInputContracts = $mobileRemoteInputContracts
    }
}

