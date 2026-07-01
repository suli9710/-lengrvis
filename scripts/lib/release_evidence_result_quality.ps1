function New-ResultQualityReviewEvidenceSummary {
    param(
        [Parameter(Mandatory = $true)]$resultQualityReviewEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

$resultQualityReviewNeedles = @(
    "result-quality-review.redacted.json",
    "NOT_RESULT_QUALITY_SIGNOFF",
    'result_quality_signoff = $false',
    'signoff = $false',
    'claim_allowed = $false',
    'completed_result_evidence = $false',
    'result_quality_claim_blocked = $true',
    'separate_human_signoff_required = $true',
    'packet_is_rc_signoff = $false',
    'packet_is_release_signoff = $false',
    "not completed-result evidence",
    "not release sign-off"
)
$resultQualityReviewContract = Get-SourceContract "scripts/collect_result_quality_review_packet.ps1" $resultQualityReviewNeedles
if (-not $resultQualityReviewContract.required_markers_present) {
    $contractFailures.Add("result-quality review helper source contract is missing required non-signoff markers")
}
$latestResultQualityReview = Find-LatestJsonArtifact $resultQualityReviewEvidenceRootPath "result-quality-review.redacted.json"
$resultQualityReviewLatestSummary = if ($latestResultQualityReview.found -and $null -ne $latestResultQualityReview.data) {
    $resultQualityMismatches = New-Object System.Collections.Generic.List[string]
    $resultQualityMarker = [string]$latestResultQualityReview.data.marker
    $resultQualityStatus = [string]$latestResultQualityReview.data.summary.status
    $allowedResultQualityStatuses = @(
        "blocked_missing_fields",
        "blocked_invalid_fields",
        "blocked_reason_recorded",
        "manual_review_fields_recorded_not_signoff"
    )
    if ($resultQualityMarker -ne "NOT_RESULT_QUALITY_SIGNOFF") {
        $resultQualityMismatches.Add("marker is missing or not NOT_RESULT_QUALITY_SIGNOFF")
    }
    if ($resultQualityStatus -notin $allowedResultQualityStatuses) {
        $resultQualityMismatches.Add("summary.status is not an allowed result-quality review status")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.signoff)) {
        $resultQualityMismatches.Add("summary.signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.result_quality_signoff)) {
        $resultQualityMismatches.Add("summary.result_quality_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.claim_allowed)) {
        $resultQualityMismatches.Add("summary.claim_allowed is not false")
    }
    if (-not (Test-JsonTrue $latestResultQualityReview.data.summary.result_quality_claim_blocked)) {
        $resultQualityMismatches.Add("summary.result_quality_claim_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestResultQualityReview.data.summary.separate_human_signoff_required)) {
        $resultQualityMismatches.Add("summary.separate_human_signoff_required is not true")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.completed_result_evidence)) {
        $resultQualityMismatches.Add("summary.completed_result_evidence is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.release_candidate_signoff)) {
        $resultQualityMismatches.Add("summary.release_candidate_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.release_signoff)) {
        $resultQualityMismatches.Add("summary.release_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.summary.template_is_signoff)) {
        $resultQualityMismatches.Add("summary.template_is_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.claim_allowed)) {
        $resultQualityMismatches.Add("claim_controls.claim_allowed is not false")
    }
    if (-not (Test-JsonTrue $latestResultQualityReview.data.claim_controls.result_quality_claim_blocked)) {
        $resultQualityMismatches.Add("claim_controls.result_quality_claim_blocked is not true")
    }
    if (-not (Test-JsonTrue $latestResultQualityReview.data.claim_controls.separate_human_signoff_required)) {
        $resultQualityMismatches.Add("claim_controls.separate_human_signoff_required is not true")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.result_quality_signoff)) {
        $resultQualityMismatches.Add("claim_controls.result_quality_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.completed_result_evidence)) {
        $resultQualityMismatches.Add("claim_controls.completed_result_evidence is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.packet_is_rc_signoff)) {
        $resultQualityMismatches.Add("claim_controls.packet_is_rc_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.claim_controls.packet_is_release_signoff)) {
        $resultQualityMismatches.Add("claim_controls.packet_is_release_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.readonly_scope.starts_product_processes)) {
        $resultQualityMismatches.Add("readonly_scope.starts_product_processes is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.readonly_scope.performs_network_requests)) {
        $resultQualityMismatches.Add("readonly_scope.performs_network_requests is not false")
    }
    if (-not (Test-JsonFalse $latestResultQualityReview.data.readonly_scope.uploads_external_services)) {
        $resultQualityMismatches.Add("readonly_scope.uploads_external_services is not false")
    }
    if (-not (Test-JsonNonNegativeInteger $latestResultQualityReview.data.summary.missing_field_count)) {
        $resultQualityMismatches.Add("summary.missing_field_count is not a non-negative JSON integer")
    }
    if (-not (Test-JsonNonNegativeInteger $latestResultQualityReview.data.summary.issue_count)) {
        $resultQualityMismatches.Add("summary.issue_count is not a non-negative JSON integer")
    }
    $expectedReviewFieldsComplete = [bool](
        $resultQualityStatus -eq "manual_review_fields_recorded_not_signoff" -and
        (Test-JsonNonNegativeInteger $latestResultQualityReview.data.summary.missing_field_count) -and
        (Test-JsonNonNegativeInteger $latestResultQualityReview.data.summary.issue_count) -and
        [int64]$latestResultQualityReview.data.summary.missing_field_count -eq 0 -and
        [int64]$latestResultQualityReview.data.summary.issue_count -eq 0
    )
    if ($latestResultQualityReview.data.summary.review_fields_complete -isnot [bool]) {
        $resultQualityMismatches.Add("summary.review_fields_complete is not a JSON boolean")
    }
    elseif ([bool]$latestResultQualityReview.data.summary.review_fields_complete -ne $expectedReviewFieldsComplete) {
        $resultQualityMismatches.Add("summary.review_fields_complete does not match missing/issue/status state")
    }
    if ($resultQualityMismatches.Count -gt 0) {
        $contractFailures.Add("latest result-quality review helper artifact failed fail-closed validation")
    }
    [ordered]@{
        found = $true
        path = $latestResultQualityReview.path
        last_write_utc = $latestResultQualityReview.last_write_utc
        marker = if ($resultQualityMarker -eq "NOT_RESULT_QUALITY_SIGNOFF") { $resultQualityMarker } else { "invalid_redacted" }
        source_contract_status = if ($resultQualityMismatches.Count -eq 0) { "valid_not_signoff_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($resultQualityMismatches)
        review_status = if ($resultQualityStatus -in $allowedResultQualityStatuses) { $resultQualityStatus } else { "invalid_redacted" }
        review_fields_complete = [bool]($latestResultQualityReview.data.summary.review_fields_complete -is [bool] -and [bool]$latestResultQualityReview.data.summary.review_fields_complete -and $resultQualityMismatches.Count -eq 0)
        missing_field_count = Get-StrictJsonNonNegativeIntegerOrZero $latestResultQualityReview.data.summary.missing_field_count
        issue_count = Get-StrictJsonNonNegativeIntegerOrZero $latestResultQualityReview.data.summary.issue_count
        blocked_reason_count = Get-ArrayCount $latestResultQualityReview.data.reviewer.blocked_reason_redacted
        observed_artifact_count = Get-ArrayCount $latestResultQualityReview.data.task_result_artifact.observed_artifacts_redacted
        result_quality_signoff = $false
        result_quality_claim_blocked = $true
        separate_human_signoff_required = $true
        signoff = $false
        claim_allowed = $false
        completed_result_evidence = $false
        release_candidate_signoff = $false
        release_signoff = $false
    }
}
elseif ($latestResultQualityReview.found) {
    $contractFailures.Add("latest result-quality review helper artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestResultQualityReview.path
        last_write_utc = $latestResultQualityReview.last_write_utc
        parse_error = $latestResultQualityReview.error
        result_quality_signoff = $false
        signoff = $false
        claim_allowed = $false
        completed_result_evidence = $false
        release_candidate_signoff = $false
        release_signoff = $false
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $resultQualityReviewEvidenceRootPath
        review_status = "not_collected_by_this_packet"
        result_quality_signoff = $false
        signoff = $false
        claim_allowed = $false
        completed_result_evidence = $false
        release_candidate_signoff = $false
        release_signoff = $false
    }
}

    return [ordered]@{
        resultQualityReviewContract = $resultQualityReviewContract
        resultQualityReviewLatestSummary = $resultQualityReviewLatestSummary
    }
}

