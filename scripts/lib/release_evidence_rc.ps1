function New-RcHandoffEvidenceSummary {
    param(
        [Parameter(Mandatory = $true)]$rcHandoffEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

$rcHandoffNeedles = @(
    "rc-handoff-template.redacted.json",
    "NOT_RELEASE_CANDIDATE_SIGNOFF",
    'release_candidate_signoff = $false',
    'claim_allowed = $false',
    'template_is_rc_pass = $false',
    'template_is_release_signoff = $false',
    'gate_commands_run_by_this_helper = $false',
    'must_not_tag_publish_or_announce = $true',
    "Do not tag, publish, announce, ship, or call the candidate passed from this template"
)
$rcHandoffContract = Get-SourceContract "scripts/collect_rc_handoff_template.ps1" $rcHandoffNeedles
if (-not $rcHandoffContract.required_markers_present) {
    $contractFailures.Add("RC handoff template source contract is missing required non-signoff markers")
}

$latestRcHandoff = Find-LatestJsonArtifact $rcHandoffEvidenceRootPath "rc-handoff-template.redacted.json"
$rcHandoffLatestSummary = if ($latestRcHandoff.found -and $null -ne $latestRcHandoff.data) {
    $rcHandoffMismatches = New-Object System.Collections.Generic.List[string]
    $rcMarker = [string]$latestRcHandoff.data.marker
    $rcStatus = [string]$latestRcHandoff.data.summary.status
    $rcMissingRequiredFieldsCount = Get-ArrayCount $latestRcHandoff.data.summary.missing_required_fields
    $allowedRcStatuses = @(
        "manual_rc_handoff_required",
        "manual_rc_handoff_recorded_unverified_by_this_helper"
    )
    $rcRequiredFields = @(
        "candidate.commit_or_build_id",
        "candidate.platform",
        "artifact_labels",
        "gate_results.commands_and_exits",
        "strict_state_source",
        "manual_p1_checks",
        "waivers",
        "residual_risks"
    )
    $rcArtifactLabelCount = Get-ArrayCount $latestRcHandoff.data.artifacts.labels
    $rcGateEntryCount = Get-ArrayCount $latestRcHandoff.data.gate_results.entries
    $rcManualP1CheckCount = Get-ArrayCount $latestRcHandoff.data.manual_p1_checks.entries
    $rcWaiverCount = Get-ArrayCount $latestRcHandoff.data.waivers.entries
    $rcResidualRiskCount = Get-ArrayCount $latestRcHandoff.data.residual_risks.entries
    $rcCandidateCommitOrBuildRecorded = (Test-MeaningfulEvidenceValue ([string]$latestRcHandoff.data.candidate.commit)) -or (Test-MeaningfulEvidenceValue ([string]$latestRcHandoff.data.candidate.build_id))
    $rcPlatformRecorded = Test-MeaningfulEvidenceValue ([string]$latestRcHandoff.data.candidate.platform)
    $rcStrictStateRecorded = Test-MeaningfulEvidenceValue ([string]$latestRcHandoff.data.strict_state_source.source)

    if (-not (Test-JsonIntegerOne $latestRcHandoff.data.schema_version)) {
        $rcHandoffMismatches.Add("schema_version is not 1")
    }
    if ([string]$latestRcHandoff.data.generated_by -ne "scripts/collect_rc_handoff_template.ps1") {
        $rcHandoffMismatches.Add("generated_by is not scripts/collect_rc_handoff_template.ps1")
    }
    if ($rcMarker -ne "NOT_RELEASE_CANDIDATE_SIGNOFF") {
        $rcHandoffMismatches.Add("marker is missing or not NOT_RELEASE_CANDIDATE_SIGNOFF")
    }
    if ($rcStatus -notin $allowedRcStatuses) {
        $rcHandoffMismatches.Add("summary.status is not a recognized non-signoff RC handoff status")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.release_candidate_signoff)) {
        $rcHandoffMismatches.Add("summary.release_candidate_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.claim_allowed)) {
        $rcHandoffMismatches.Add("summary.claim_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.template_is_rc_pass)) {
        $rcHandoffMismatches.Add("summary.template_is_rc_pass is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.template_is_release_signoff)) {
        $rcHandoffMismatches.Add("summary.template_is_release_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.summary.gate_commands_run_by_this_helper)) {
        $rcHandoffMismatches.Add("summary.gate_commands_run_by_this_helper is not false")
    }
    if (-not (Test-JsonNonNegativeInteger $latestRcHandoff.data.summary.missing_required_fields_count)) {
        $rcHandoffMismatches.Add("summary.missing_required_fields_count is not a non-negative JSON integer")
    }
    elseif ([int64]$latestRcHandoff.data.summary.missing_required_fields_count -ne [int64]$rcMissingRequiredFieldsCount) {
        $rcHandoffMismatches.Add("summary.missing_required_fields_count does not match missing_required_fields")
    }
    if ($rcStatus -eq "manual_rc_handoff_recorded_unverified_by_this_helper" -and $rcMissingRequiredFieldsCount -ne 0) {
        $rcHandoffMismatches.Add("recorded RC handoff status still has missing required fields")
    }
    if ($rcStatus -eq "manual_rc_handoff_required" -and $rcMissingRequiredFieldsCount -eq 0) {
        $rcHandoffMismatches.Add("required RC handoff status has no missing required fields")
    }
    foreach ($requiredField in $rcRequiredFields) {
        if (-not (Test-ArrayContainsText $latestRcHandoff.data.required_fields $requiredField)) {
            $rcHandoffMismatches.Add("required_fields is missing $requiredField")
        }
    }
    if ($rcStatus -eq "manual_rc_handoff_recorded_unverified_by_this_helper") {
        if (-not $rcCandidateCommitOrBuildRecorded) {
            $rcHandoffMismatches.Add("recorded RC handoff is missing candidate commit or build id")
        }
        if ([string]$latestRcHandoff.data.candidate.commit_or_build_id_status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("candidate.commit_or_build_id_status is not recorded")
        }
        if (-not $rcPlatformRecorded) {
            $rcHandoffMismatches.Add("recorded RC handoff is missing candidate platform")
        }
        if ([string]$latestRcHandoff.data.candidate.platform_status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("candidate.platform_status is not recorded")
        }
        if ($rcArtifactLabelCount -eq 0 -or [string]$latestRcHandoff.data.artifacts.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing recorded artifact labels")
        }
        if ($rcGateEntryCount -eq 0 -or [string]$latestRcHandoff.data.gate_results.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing recorded gate command/exit entries")
        }
        if (-not (Test-JsonTrue $latestRcHandoff.data.gate_results.commands_and_exits_count_match)) {
            $rcHandoffMismatches.Add("recorded RC handoff gate command/exit counts do not match")
        }
        if (-not $rcStrictStateRecorded -or [string]$latestRcHandoff.data.strict_state_source.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing strict-state source")
        }
        if ($rcManualP1CheckCount -eq 0 -or [string]$latestRcHandoff.data.manual_p1_checks.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing manual P1 checks")
        }
        if ($rcWaiverCount -eq 0 -or [string]$latestRcHandoff.data.waivers.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing waiver record")
        }
        if ($rcResidualRiskCount -eq 0 -or [string]$latestRcHandoff.data.residual_risks.status -ne "recorded_unverified_by_this_helper") {
            $rcHandoffMismatches.Add("recorded RC handoff is missing residual risk record")
        }
        foreach ($entry in @($latestRcHandoff.data.artifacts.labels)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.value))) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an empty artifact label")
                break
            }
        }
        foreach ($entry in @($latestRcHandoff.data.gate_results.entries)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.command)) -or -not (Test-RcGateExitValue ([string]$entry.exit_status)) -or -not (Test-JsonTrue $entry.exact_command_and_exit_recorded)) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an incomplete gate command/exit entry")
                break
            }
        }
        foreach ($entry in @($latestRcHandoff.data.manual_p1_checks.entries)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.value))) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an empty manual P1 check")
                break
            }
        }
        foreach ($entry in @($latestRcHandoff.data.waivers.entries)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.value))) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an empty waiver record")
                break
            }
        }
        foreach ($entry in @($latestRcHandoff.data.residual_risks.entries)) {
            if ($null -eq $entry -or -not (Test-MeaningfulEvidenceValue ([string]$entry.value))) {
                $rcHandoffMismatches.Add("recorded RC handoff contains an empty residual risk")
                break
            }
        }
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.signoff_controls.release_candidate_signoff)) {
        $rcHandoffMismatches.Add("signoff_controls.release_candidate_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.signoff_controls.claim_allowed)) {
        $rcHandoffMismatches.Add("signoff_controls.claim_allowed is not false")
    }
    if (-not (Test-JsonTrue $latestRcHandoff.data.signoff_controls.pass_defaults_remain_false)) {
        $rcHandoffMismatches.Add("signoff_controls.pass_defaults_remain_false is not true")
    }
    if (-not (Test-JsonTrue $latestRcHandoff.data.signoff_controls.must_not_tag_publish_or_announce)) {
        $rcHandoffMismatches.Add("signoff_controls.must_not_tag_publish_or_announce is not true")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.readonly_scope.starts_product_processes)) {
        $rcHandoffMismatches.Add("readonly_scope.starts_product_processes is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.readonly_scope.runs_release_commands)) {
        $rcHandoffMismatches.Add("readonly_scope.runs_release_commands is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.readonly_scope.performs_network_requests)) {
        $rcHandoffMismatches.Add("readonly_scope.performs_network_requests is not false")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.readonly_scope.installs_dependencies)) {
        $rcHandoffMismatches.Add("readonly_scope.installs_dependencies is not false")
    }
    if (-not (Test-JsonTrue $latestRcHandoff.data.readonly_scope.writes_only_rc_handoff_template_artifacts)) {
        $rcHandoffMismatches.Add("readonly_scope.writes_only_rc_handoff_template_artifacts is not true")
    }
    if (-not (Test-JsonTrue $latestRcHandoff.data.gate_results.exact_commands_and_exits_required)) {
        $rcHandoffMismatches.Add("gate_results.exact_commands_and_exits_required is not true")
    }
    if (-not (Test-JsonFalse $latestRcHandoff.data.gate_results.commands_run_by_this_helper)) {
        $rcHandoffMismatches.Add("gate_results.commands_run_by_this_helper is not false")
    }
    foreach ($entry in @($latestRcHandoff.data.gate_results.entries)) {
        if (-not (Test-JsonFalse $entry.pass_verified_by_this_helper)) {
            $rcHandoffMismatches.Add("gate_results.entries.pass_verified_by_this_helper is not false")
            break
        }
    }
    if (-not (Test-ArrayContainsText $latestRcHandoff.data.must_not_be_recorded_as "release-candidate pass")) {
        $rcHandoffMismatches.Add("must_not_be_recorded_as is missing release-candidate pass")
    }
    if (-not (Test-ArrayContainsText $latestRcHandoff.data.must_not_be_recorded_as "release sign-off")) {
        $rcHandoffMismatches.Add("must_not_be_recorded_as is missing release sign-off")
    }
    if (-not (Test-ArrayContainsText $latestRcHandoff.data.must_not_be_recorded_as "permission to tag, publish, announce, or ship")) {
        $rcHandoffMismatches.Add("must_not_be_recorded_as is missing permission-to-ship warning")
    }

    if ($rcHandoffMismatches.Count -gt 0) {
        $contractFailures.Add("latest RC handoff helper artifact failed fail-closed validation")
    }

    [ordered]@{
        found = $true
        path = $latestRcHandoff.path
        last_write_utc = $latestRcHandoff.last_write_utc
        marker = if ($rcMarker -eq "NOT_RELEASE_CANDIDATE_SIGNOFF") { $rcMarker } else { "invalid_redacted" }
        source_contract_status = if ($rcHandoffMismatches.Count -eq 0) { "valid_not_signoff_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($rcHandoffMismatches)
        handoff_status = if ($rcStatus -in $allowedRcStatuses) { $rcStatus } else { "invalid_redacted" }
        release_candidate_signoff = $false
        claim_allowed = $false
        template_is_rc_pass = $false
        template_is_release_signoff = $false
        gate_commands_run_by_this_helper = $false
        must_not_tag_publish_or_announce = $true
        missing_required_fields_count = Get-StrictJsonNonNegativeIntegerOrZero $latestRcHandoff.data.summary.missing_required_fields_count
        missing_required_fields = @($latestRcHandoff.data.summary.missing_required_fields)
        required_fields_recorded = [bool]($rcStatus -eq "manual_rc_handoff_recorded_unverified_by_this_helper" -and $rcMissingRequiredFieldsCount -eq 0 -and $rcHandoffMismatches.Count -eq 0)
        artifact_label_count = $rcArtifactLabelCount
        gate_result_count = $rcGateEntryCount
        manual_p1_check_count = $rcManualP1CheckCount
        waiver_count = $rcWaiverCount
        residual_risk_count = $rcResidualRiskCount
        candidate = [ordered]@{
            commit = Redact-TextValue ([string]$latestRcHandoff.data.candidate.commit)
            build_id = Redact-TextValue ([string]$latestRcHandoff.data.candidate.build_id)
            platform = Redact-TextValue ([string]$latestRcHandoff.data.candidate.platform)
            commit_or_build_id_status = Redact-TextValue ([string]$latestRcHandoff.data.candidate.commit_or_build_id_status)
            platform_status = Redact-TextValue ([string]$latestRcHandoff.data.candidate.platform_status)
        }
    }
}
elseif ($latestRcHandoff.found) {
    $contractFailures.Add("latest RC handoff helper artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestRcHandoff.path
        last_write_utc = $latestRcHandoff.last_write_utc
        parse_error = $latestRcHandoff.error
        source_contract_status = "parse_error"
        release_candidate_signoff = $false
        claim_allowed = $false
        template_is_rc_pass = $false
        template_is_release_signoff = $false
        gate_commands_run_by_this_helper = $false
        must_not_tag_publish_or_announce = $true
        required_fields_recorded = $false
        artifact_label_count = 0
        gate_result_count = 0
        manual_p1_check_count = 0
        waiver_count = 0
        residual_risk_count = 0
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $rcHandoffEvidenceRootPath
        source_contract_status = "not_collected_by_this_packet"
        handoff_status = "not_collected_by_this_packet"
        release_candidate_signoff = $false
        claim_allowed = $false
        template_is_rc_pass = $false
        template_is_release_signoff = $false
        gate_commands_run_by_this_helper = $false
        must_not_tag_publish_or_announce = $true
        missing_required_fields_count = 0
        missing_required_fields = @()
        required_fields_recorded = $false
        artifact_label_count = 0
        gate_result_count = 0
        manual_p1_check_count = 0
        waiver_count = 0
        residual_risk_count = 0
    }
}

    return [ordered]@{
        rcHandoffContract = $rcHandoffContract
        rcHandoffLatestSummary = $rcHandoffLatestSummary
    }
}

