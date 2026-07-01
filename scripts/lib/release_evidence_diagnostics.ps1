function New-DiagnosticsExternalReviewEvidenceSummary {
    param(
        [Parameter(Mandatory = $true)]$diagnosticsReviewEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

$diagnosticsNeedles = @(
    "support_package_redaction",
    'external_review["status"] == "manual_review_required"',
    'external_review["required_before_external_sharing"] is True',
    'external_review["public_safe"] is False',
    'assert ''"public_safe": false'' in package_text'
)
$diagnosticsContract = Get-SourceContract "backend/tests/test_system_diagnostics.py" $diagnosticsNeedles
if (-not $diagnosticsContract.required_markers_present) {
    $contractFailures.Add("diagnostics support-package external-review contract is missing required markers")
}
$latestDiagnosticsReview = Find-LatestJsonArtifact $diagnosticsReviewEvidenceRootPath "diagnostics-external-review.redacted.json"
$diagnosticsReviewLatestSummary = if ($latestDiagnosticsReview.found -and $null -ne $latestDiagnosticsReview.data) {
    $diagnosticsReviewMismatches = New-Object System.Collections.Generic.List[string]
    $diagnosticsReviewMarker = [string]$latestDiagnosticsReview.data.marker
    $reviewStatus = [string]$latestDiagnosticsReview.data.summary.review_status
    if ([string]::IsNullOrWhiteSpace($reviewStatus)) {
        $reviewStatus = [string]$latestDiagnosticsReview.data.summary.status
    }
    $allowedDiagnosticsReviewStatuses = @(
        "manual_external_review_template_ready",
        "blocked_missing_diagnostics_package",
        "blocked_unreadable_diagnostics_package",
        "blocked_contract_mismatch"
    )
    if ($diagnosticsReviewMarker -ne "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF") {
        $diagnosticsReviewMismatches.Add("marker is missing or not NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF")
    }
    if ($reviewStatus -notin $allowedDiagnosticsReviewStatuses) {
        $diagnosticsReviewMismatches.Add("review status is not a recognized fail-closed diagnostics review status")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.public_safe)) {
        $diagnosticsReviewMismatches.Add("summary.public_safe is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.summary.required_before_external_sharing)) {
        $diagnosticsReviewMismatches.Add("summary.required_before_external_sharing is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.human_review_signoff)) {
        $diagnosticsReviewMismatches.Add("summary.human_review_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.external_public_safe_signoff)) {
        $diagnosticsReviewMismatches.Add("summary.external_public_safe_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.template_is_human_signoff)) {
        $diagnosticsReviewMismatches.Add("summary.template_is_human_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.external_sharing_allowed)) {
        $diagnosticsReviewMismatches.Add("summary.external_sharing_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.claim_allowed)) {
        $diagnosticsReviewMismatches.Add("summary.claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.summary.actual_package_content_review_completed)) {
        $diagnosticsReviewMismatches.Add("summary.actual_package_content_review_completed is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.summary.automated_template_only)) {
        $diagnosticsReviewMismatches.Add("summary.automated_template_only is not true")
    }
    if ($latestDiagnosticsReview.data.summary.review_fields_complete -isnot [bool]) {
        $diagnosticsReviewMismatches.Add("summary.review_fields_complete is not a JSON boolean")
    }
    elseif ([bool]$latestDiagnosticsReview.data.summary.review_fields_complete) {
        $diagnosticsReviewMismatches.Add("summary.review_fields_complete is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.summary.external_sharing_blocked)) {
        $diagnosticsReviewMismatches.Add("summary.external_sharing_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.summary.separate_human_content_review_required)) {
        $diagnosticsReviewMismatches.Add("summary.separate_human_content_review_required is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_scope.automated_redaction_template)) {
        $diagnosticsReviewMismatches.Add("review_scope.automated_redaction_template is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_scope.actual_package_content_review_completed)) {
        $diagnosticsReviewMismatches.Add("review_scope.actual_package_content_review_completed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_scope.automated_template_is_actual_package_content_review)) {
        $diagnosticsReviewMismatches.Add("review_scope.automated_template_is_actual_package_content_review is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_scope.actual_content_review_required_before_external_sharing)) {
        $diagnosticsReviewMismatches.Add("review_scope.actual_content_review_required_before_external_sharing is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_scope.review_fields_complete)) {
        $diagnosticsReviewMismatches.Add("review_scope.review_fields_complete is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_scope.external_sharing_blocked)) {
        $diagnosticsReviewMismatches.Add("review_scope.external_sharing_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_scope.separate_human_content_review_required)) {
        $diagnosticsReviewMismatches.Add("review_scope.separate_human_content_review_required is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.public_safe)) {
        $diagnosticsReviewMismatches.Add("claim_controls.public_safe is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.external_sharing_allowed)) {
        $diagnosticsReviewMismatches.Add("claim_controls.external_sharing_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.claim_allowed)) {
        $diagnosticsReviewMismatches.Add("claim_controls.claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.helper_can_approve_public_safety)) {
        $diagnosticsReviewMismatches.Add("claim_controls.helper_can_approve_public_safety is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.helper_can_authorize_external_sharing)) {
        $diagnosticsReviewMismatches.Add("claim_controls.helper_can_authorize_external_sharing is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.claim_controls.actual_content_review_required)) {
        $diagnosticsReviewMismatches.Add("claim_controls.actual_content_review_required is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.actual_content_review_completed)) {
        $diagnosticsReviewMismatches.Add("claim_controls.actual_content_review_completed is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.claim_controls.external_sharing_blocked)) {
        $diagnosticsReviewMismatches.Add("claim_controls.external_sharing_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.claim_controls.separate_human_content_review_required)) {
        $diagnosticsReviewMismatches.Add("claim_controls.separate_human_content_review_required is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.claim_controls.public_safe_approval_created)) {
        $diagnosticsReviewMismatches.Add("claim_controls.public_safe_approval_created is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.public_safe)) {
        $diagnosticsReviewMismatches.Add("review_template.public_safe is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.external_sharing_allowed)) {
        $diagnosticsReviewMismatches.Add("review_template.external_sharing_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.claim_allowed)) {
        $diagnosticsReviewMismatches.Add("review_template.claim_allowed is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_template.required_before_external_sharing)) {
        $diagnosticsReviewMismatches.Add("review_template.required_before_external_sharing is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.review_fields_complete)) {
        $diagnosticsReviewMismatches.Add("review_template.review_fields_complete is not false")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_template.external_sharing_blocked)) {
        $diagnosticsReviewMismatches.Add("review_template.external_sharing_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestDiagnosticsReview.data.review_template.separate_human_content_review_required)) {
        $diagnosticsReviewMismatches.Add("review_template.separate_human_content_review_required is not true")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.actual_package_content_review_completed)) {
        $diagnosticsReviewMismatches.Add("review_template.actual_package_content_review_completed is not false")
    }
    if ($diagnosticsReviewMismatches.Count -gt 0) {
        $contractFailures.Add("latest diagnostics external-review helper artifact failed fail-closed validation")
    }
    $safeDiagnosticsReviewMarker = if ($diagnosticsReviewMarker -eq "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF") { $diagnosticsReviewMarker } else { "invalid_redacted" }
    $safeReviewStatus = if ($reviewStatus -in $allowedDiagnosticsReviewStatuses) { $reviewStatus } else { "invalid_redacted" }
    [ordered]@{
        found = $true
        path = $latestDiagnosticsReview.path
        last_write_utc = $latestDiagnosticsReview.last_write_utc
        marker = $safeDiagnosticsReviewMarker
        source_contract_status = if ($diagnosticsReviewMismatches.Count -eq 0) {
            if ($reviewStatus -eq "manual_external_review_template_ready") { "valid_not_signoff_template" } else { "valid_fail_closed_template" }
        } else { "source_contract_mismatch" }
        mismatch_reasons = @($diagnosticsReviewMismatches)
        review_status = $safeReviewStatus
        public_safe = $false
        external_sharing_allowed = $false
        claim_allowed = $false
        human_review_signoff = $false
        template_is_human_signoff = $false
        review_fields_complete = $false
        actual_package_content_review_completed = $false
        external_sharing_blocked = $true
        separate_human_content_review_required = $true
        checklist_count = Get-ArrayCount $latestDiagnosticsReview.data.review_template.checklist
    }
}
elseif ($latestDiagnosticsReview.found) {
    $contractFailures.Add("latest diagnostics external-review helper artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestDiagnosticsReview.path
        last_write_utc = $latestDiagnosticsReview.last_write_utc
        parse_error = $latestDiagnosticsReview.error
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $diagnosticsReviewEvidenceRootPath
        review_status = "not_collected_by_this_packet"
        public_safe = $false
        external_sharing_allowed = $false
        claim_allowed = $false
        review_fields_complete = $false
        actual_package_content_review_completed = $false
        external_sharing_blocked = $true
        separate_human_content_review_required = $true
    }
}

    return [ordered]@{
        diagnosticsContract = $diagnosticsContract
        diagnosticsReviewLatestSummary = $diagnosticsReviewLatestSummary
    }
}

