function New-MobileLanWssLatestSummary {
    param(
        [Parameter(Mandatory = $true)]$mobileEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

$latestMobile = Find-LatestJsonArtifact $mobileEvidenceRootPath "evidence-summary.redacted.json"
$mobileLatestSummary = if ($latestMobile.found -and $null -ne $latestMobile.data) {
    $mobileArtifactMismatches = New-Object System.Collections.Generic.List[string]
    $mobileResult = [string]$latestMobile.data.result
    $mobileGeneratedAt = [string]$latestMobile.data.generated_at_utc
    $mobileGeneratedAtParsed = [DateTimeOffset]::MinValue
    $mobileHostRedacted = [string]$latestMobile.data.backend.host_redacted
    $mobilePublicBaseUrlRedacted = [string]$latestMobile.data.backend.public_base_url_redacted
    $mobileApprovalsUrlRedacted = [string]$latestMobile.data.backend.websocket_approvals_url_redacted
    $mobileRemoteScreenUrlRedacted = [string]$latestMobile.data.backend.websocket_remote_screen_url_redacted
    $mobileRemoteInputUrlRedacted = [string]$latestMobile.data.backend.websocket_remote_input_url_redacted
    $mobileQrApprovalsUrlRedacted = [string]$latestMobile.data.qr_payload_shape.websocket_approvals_url_redacted
    $mobileQrRemoteScreenUrlRedacted = [string]$latestMobile.data.qr_payload_shape.websocket_remote_screen_url_redacted
    $mobileQrRemoteInputUrlRedacted = [string]$latestMobile.data.qr_payload_shape.websocket_remote_input_url_redacted
    $mobileRealDeviceEvidenceStatus = [string]$latestMobile.data.real_device_evidence_status
    $mobileNoPhoneClaim = [string]$latestMobile.data.no_phone_preflight_claim
    $mobileTransportSecurityStatus = [string]$latestMobile.data.qr_payload_shape.transport_security_status
    $allowedMobileResults = @("ready_for_manual_real_device_collection_only", "blocked")
    $mobileReadyStatus = $mobileResult -eq "ready_for_manual_real_device_collection_only"
    $mobileChecklistNames = @(
        "camera_qr",
        "actual_https_wss",
        "approval_wss",
        "remote_screen_wss",
        "remote_input_wss",
        "certificate_trust",
        "remote_input_grant_revoke_expiry",
        "screenshot_log_review"
    )
    $mobileCollectionChecklistStatuses = [ordered]@{}
    if ($mobileResult -notin $allowedMobileResults) {
        $mobileArtifactMismatches.Add("result is not an allowed mobile preflight status")
    }
    if (-not [DateTimeOffset]::TryParse($mobileGeneratedAt, [ref]$mobileGeneratedAtParsed)) {
        $mobileArtifactMismatches.Add("generated_at_utc is not a timestamp")
    }
    if (-not (Test-MobileRedactedHostLabel $mobileHostRedacted)) {
        $mobileArtifactMismatches.Add("backend.host_redacted is not a safe redacted host label")
    }
    if (-not (Test-MobileRedactedHttpOrigin $mobilePublicBaseUrlRedacted)) {
        $mobileArtifactMismatches.Add("backend.public_base_url_redacted is not a safe redacted origin")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobilePublicBaseUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.public_base_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileApprovalsUrlRedacted "/ws/mobile/approvals")) {
        $mobileArtifactMismatches.Add("backend.websocket_approvals_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileApprovalsUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.websocket_approvals_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileRemoteScreenUrlRedacted "/ws/remote/screen")) {
        $mobileArtifactMismatches.Add("backend.websocket_remote_screen_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileRemoteScreenUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.websocket_remote_screen_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileRemoteInputUrlRedacted "/ws/remote/input")) {
        $mobileArtifactMismatches.Add("backend.websocket_remote_input_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileRemoteInputUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.websocket_remote_input_url_redacted")
    }
    if (-not (Test-JsonBool $latestMobile.data.lan_tls.enabled)) {
        $mobileArtifactMismatches.Add("lan_tls.enabled is not a JSON boolean")
    }
    if (-not (Test-JsonBool $latestMobile.data.lan_tls.tls_material_valid)) {
        $mobileArtifactMismatches.Add("lan_tls.tls_material_valid is not a JSON boolean")
    }
    if (-not (Test-JsonBool $latestMobile.data.lan_tls.tls_host_valid)) {
        $mobileArtifactMismatches.Add("lan_tls.tls_host_valid is not a JSON boolean")
    }
    if ($mobileTransportSecurityStatus -notin @("https_ready_preflight", "https_wss_preflight_blocked")) {
        $mobileArtifactMismatches.Add("qr_payload_shape.transport_security_status is not an allowed status")
    }
    if (-not (Test-JsonBool $latestMobile.data.qr_payload_shape.transport_security_tls_ready)) {
        $mobileArtifactMismatches.Add("qr_payload_shape.transport_security_tls_ready is not a JSON boolean")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileQrApprovalsUrlRedacted "/ws/mobile/approvals")) {
        $mobileArtifactMismatches.Add("qr_payload_shape.websocket_approvals_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrApprovalsUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing qr_payload_shape.websocket_approvals_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileQrRemoteScreenUrlRedacted "/ws/remote/screen")) {
        $mobileArtifactMismatches.Add("qr_payload_shape.websocket_remote_screen_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrRemoteScreenUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing qr_payload_shape.websocket_remote_screen_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketPath $mobileQrRemoteInputUrlRedacted "/ws/remote/input")) {
        $mobileArtifactMismatches.Add("qr_payload_shape.websocket_remote_input_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrRemoteInputUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing qr_payload_shape.websocket_remote_input_url_redacted")
    }
    if ($mobileRealDeviceEvidenceStatus -ne "uncollected_fail_closed") {
        $mobileArtifactMismatches.Add("real_device_evidence_status is not uncollected_fail_closed")
    }
    if (-not (Test-JsonFalse $latestMobile.data.real_device_evidence_collected)) {
        $mobileArtifactMismatches.Add("real_device_evidence_collected is not false")
    }
    if ($mobileNoPhoneClaim -ne "not_real_device_pass") {
        $mobileArtifactMismatches.Add("no_phone_preflight_claim is not not_real_device_pass")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.template_status -ne "manual_real_device_evidence_required") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.template_status is not manual_real_device_evidence_required")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.real_device_result -ne "uncollected") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_result is not uncollected")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.real_device_evidence_status -ne "uncollected_fail_closed") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_evidence_status is not uncollected_fail_closed")
    }
    if (-not (Test-JsonFalse $latestMobile.data.manual_real_device_evidence_template.real_device_evidence_collected)) {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_evidence_collected is not false")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.no_phone_preflight_claim -ne "not_real_device_pass") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.no_phone_preflight_claim is not not_real_device_pass")
    }
    if (-not (Test-JsonFalse $latestMobile.data.manual_real_device_evidence_template.claim_controls.real_device_pass_claim_allowed)) {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.claim_controls.real_device_pass_claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestMobile.data.manual_real_device_evidence_template.claim_controls.preflight_ready_is_pass)) {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.claim_controls.preflight_ready_is_pass is not false")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.may_be_recorded_as -ne "preflight/config evidence only") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.may_be_recorded_as is not preflight/config evidence only")
    }
    if ([string]$latestMobile.data.manual_real_device_evidence_template.must_not_be_recorded_as -ne "real-device pass evidence") {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.must_not_be_recorded_as is not real-device pass evidence")
    }
    if (-not (Test-JsonTrue $latestMobile.data.manual_real_device_evidence_template.artifact_collection_rules.review_required_before_pass_claim)) {
        $mobileArtifactMismatches.Add("manual_real_device_evidence_template.artifact_collection_rules.review_required_before_pass_claim is not true")
    }
    foreach ($fieldName in @(
        "camera_qr_path_evidence",
        "actual_device_https_wss_evidence",
        "approval_wss_evidence",
        "approval_artifact_review",
        "remote_screen_wss_evidence",
        "remote_screen_artifact_review",
        "remote_input_wss_evidence",
        "remote_input_artifact_review",
        "certificate_trust_evidence",
        "remote_input_grant_revoke_evidence",
        "remote_input_grant_expiry_evidence",
        "grant_revoke_expiry_artifact_review",
        "artifact_redaction_review"
    )) {
        if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.$fieldName -ne "uncollected") {
            $mobileArtifactMismatches.Add("manual_real_device_evidence_template.fields.$fieldName is not uncollected")
        }
    }
    foreach ($checklistName in $mobileChecklistNames) {
        $checklistEntry = $latestMobile.data.manual_real_device_evidence_template.real_device_collection_checklist.$checklistName
        $checklistStatus = [string]$checklistEntry.status
        $mobileCollectionChecklistStatuses[$checklistName] = if ($checklistStatus -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
        if ($checklistStatus -ne "uncollected") {
            $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_collection_checklist.$checklistName.status is not uncollected")
        }
        if ([string]::IsNullOrWhiteSpace([string]$checklistEntry.overclaim_guard)) {
            $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_collection_checklist.$checklistName.overclaim_guard is missing")
        }
        if ([string]::IsNullOrWhiteSpace([string]$checklistEntry.reviewer_check)) {
            $mobileArtifactMismatches.Add("manual_real_device_evidence_template.real_device_collection_checklist.$checklistName.reviewer_check is missing")
        }
    }
    if ($mobileArtifactMismatches.Count -gt 0) {
        $contractFailures.Add("latest mobile LAN/WSS preflight artifact failed redacted contract validation")
    }
    [ordered]@{
        found = $true
        path = $latestMobile.path
        last_write_utc = $latestMobile.last_write_utc
        source_contract_status = if ($mobileArtifactMismatches.Count -eq 0) { "valid_redacted_summary" } else { "source_contract_mismatch" }
        mismatch_reasons = @($mobileArtifactMismatches)
        result = if ($mobileArtifactMismatches.Count -eq 0 -and $mobileResult -in $allowedMobileResults) { $mobileResult } elseif ($mobileResult -in $allowedMobileResults) { "source_contract_mismatch" } else { "invalid_redacted" }
        generated_at_utc = if ($mobileArtifactMismatches -notcontains "generated_at_utc is not a timestamp") { $mobileGeneratedAt } else { "invalid_redacted" }
        real_device_evidence_status = if ($mobileRealDeviceEvidenceStatus -eq "uncollected_fail_closed") { $mobileRealDeviceEvidenceStatus } else { "invalid_redacted" }
        real_device_evidence_collected = $false
        no_phone_preflight_claim = if ($mobileNoPhoneClaim -eq "not_real_device_pass") { $mobileNoPhoneClaim } else { "invalid_redacted" }
        backend = [ordered]@{
            host_redacted = Get-SafeMobileHostLabel $mobileHostRedacted
            public_base_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobilePublicBaseUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileHttpOrigin $mobilePublicBaseUrlRedacted }
            websocket_approvals_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileApprovalsUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileApprovalsUrlRedacted }
            websocket_remote_screen_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileRemoteScreenUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileRemoteScreenUrlRedacted }
            websocket_remote_input_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileRemoteInputUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileRemoteInputUrlRedacted }
        }
        lan_tls = [ordered]@{
            enabled = Get-StrictJsonBoolValue $latestMobile.data.lan_tls.enabled
            tls_material_valid = Get-StrictJsonBoolValue $latestMobile.data.lan_tls.tls_material_valid
            tls_host_valid = Get-StrictJsonBoolValue $latestMobile.data.lan_tls.tls_host_valid
        }
        qr_payload_shape = [ordered]@{
            transport_security_status = if ($mobileArtifactMismatches.Count -eq 0 -and $mobileTransportSecurityStatus -in @("https_ready_preflight", "https_wss_preflight_blocked")) { $mobileTransportSecurityStatus } elseif ($mobileTransportSecurityStatus -in @("https_ready_preflight", "https_wss_preflight_blocked")) { "source_contract_mismatch" } else { "invalid_redacted" }
            transport_security_tls_ready = Get-StrictJsonBoolValue $latestMobile.data.qr_payload_shape.transport_security_tls_ready
            websocket_approvals_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrApprovalsUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileQrApprovalsUrlRedacted }
            websocket_remote_screen_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrRemoteScreenUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileQrRemoteScreenUrlRedacted }
            websocket_remote_input_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileQrRemoteInputUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileQrRemoteInputUrlRedacted }
        }
        manual_real_device_evidence_template = [ordered]@{
            template_status = if ([string]$latestMobile.data.manual_real_device_evidence_template.template_status -eq "manual_real_device_evidence_required") { "manual_real_device_evidence_required" } else { "invalid_redacted" }
            real_device_result = if ([string]$latestMobile.data.manual_real_device_evidence_template.real_device_result -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
            real_device_evidence_status = if ([string]$latestMobile.data.manual_real_device_evidence_template.real_device_evidence_status -eq "uncollected_fail_closed") { "uncollected_fail_closed" } else { "invalid_redacted" }
            real_device_evidence_collected = $false
            no_phone_preflight_claim = if ([string]$latestMobile.data.manual_real_device_evidence_template.no_phone_preflight_claim -eq "not_real_device_pass") { "not_real_device_pass" } else { "invalid_redacted" }
            may_be_recorded_as = if ([string]$latestMobile.data.manual_real_device_evidence_template.may_be_recorded_as -eq "preflight/config evidence only") { "preflight/config evidence only" } else { "invalid_redacted" }
            must_not_be_recorded_as = if ([string]$latestMobile.data.manual_real_device_evidence_template.must_not_be_recorded_as -eq "real-device pass evidence") { "real-device pass evidence" } else { "invalid_redacted" }
            claim_controls = [ordered]@{
                real_device_pass_claim_allowed = $false
                preflight_ready_is_pass = $false
            }
            artifact_collection_rules = [ordered]@{
                review_required_before_pass_claim = Get-StrictJsonBoolValue $latestMobile.data.manual_real_device_evidence_template.artifact_collection_rules.review_required_before_pass_claim
            }
            fields = [ordered]@{
                camera_qr_path_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.camera_qr_path_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                actual_device_https_wss_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.actual_device_https_wss_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                approval_wss_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.approval_wss_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                approval_artifact_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.approval_artifact_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_screen_wss_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_screen_wss_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_screen_artifact_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_screen_artifact_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_input_wss_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_input_wss_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_input_artifact_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_input_artifact_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                certificate_trust_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.certificate_trust_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_input_grant_revoke_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_input_grant_revoke_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                remote_input_grant_expiry_evidence = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.remote_input_grant_expiry_evidence -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                grant_revoke_expiry_artifact_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.grant_revoke_expiry_artifact_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
                artifact_redaction_review = if ([string]$latestMobile.data.manual_real_device_evidence_template.fields.artifact_redaction_review -eq "uncollected") { "uncollected" } else { "invalid_redacted" }
            }
            collection_checklist_statuses = $mobileCollectionChecklistStatuses
        }
        issues_count = Get-ArrayCount $latestMobile.data.issues
        warnings_count = Get-ArrayCount $latestMobile.data.warnings
    }
}
elseif ($latestMobile.found) {
    $contractFailures.Add("latest mobile LAN/WSS preflight artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestMobile.path
        last_write_utc = $latestMobile.last_write_utc
        source_contract_status = "source_contract_mismatch"
        parse_error = $latestMobile.error
        result = "source_contract_mismatch"
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $mobileEvidenceRootPath
        result = "not_collected_by_this_packet"
    }
}

    return $mobileLatestSummary
}

