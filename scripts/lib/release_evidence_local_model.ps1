function New-LocalModelEvidenceSummary {
    param(
        [Parameter(Mandatory = $true)]$localModelCleanMachineEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

$ollamaContractFiles = @(
    "backend/tests/test_ollama_service.py",
    "backend/tests/test_ollama_install_endpoint.py"
)
$ollamaCounts = @($ollamaContractFiles | ForEach-Object { Count-TestContracts $_ })
$ollamaContractCount = 0
foreach ($item in $ollamaCounts) {
    $ollamaContractCount += [int]$item.test_contract_count
    if (-not $item.exists) {
        $contractFailures.Add("Ollama/local-model contract file is missing: $($item.path)")
    }
}

$localModelTemplateNeedles = @(
    "local-model-clean-machine-evidence.redacted.json",
    "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS",
    'clean_machine_signoff = $false',
    'local_model_install_pass = $false',
    'local_model_start_pass = $false',
    'local_model_pull_pass = $false',
    'local_model_task_smoke_pass = $false',
    'real_install_start_pull_pass = $false',
    'template_is_clean_machine_pass = $false',
    'dev_smoke_is_clean_machine_pass = $false',
    "manual_clean_machine_local_model_evidence_required",
    "artifact_build_profile",
    "clean_machine_run",
    "task_smoke",
    "true local model install pass",
    "true local model pull pass",
    "template/dev smoke clean-machine pass"
)
$localModelTemplateContract = Get-SourceContract "scripts/collect_local_model_clean_machine_evidence_template.ps1" $localModelTemplateNeedles
if (-not $localModelTemplateContract.required_markers_present) {
    $contractFailures.Add("local-model clean-machine evidence template source contract is missing required non-signoff markers")
}
$latestLocalModelTemplate = Find-LatestJsonArtifact $localModelCleanMachineEvidenceRootPath "local-model-clean-machine-evidence.redacted.json"
$localModelTemplateLatestSummary = if ($latestLocalModelTemplate.found -and $null -ne $latestLocalModelTemplate.data) {
    $localModelTemplateMismatches = New-Object System.Collections.Generic.List[string]
    $localModelTemplateMarker = [string]$latestLocalModelTemplate.data.marker
    $localModelTemplateStatus = [string]$latestLocalModelTemplate.data.summary.template_status
    $evidenceTemplateStatus = [string]$latestLocalModelTemplate.data.evidence_template.template_status
    $localModelMissingFieldCount = Get-ArrayCount $latestLocalModelTemplate.data.summary.missing_required_fields
    if ($localModelTemplateMarker -ne "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS") {
        $localModelTemplateMismatches.Add("marker is missing or not NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS")
    }
    if (-not (Test-JsonIntegerOne $latestLocalModelTemplate.data.schema_version)) {
        $localModelTemplateMismatches.Add("schema_version is not 1")
    }
    if ([string]$latestLocalModelTemplate.data.generated_by -ne "scripts/collect_local_model_clean_machine_evidence_template.ps1") {
        $localModelTemplateMismatches.Add("generated_by is not the local-model clean-machine helper")
    }
    if ($localModelTemplateStatus -notin @("manual_review_ready", "blocked_reason_recorded", "blocked_missing_required_fields")) {
        $localModelTemplateMismatches.Add("summary.template_status is not an allowed non-signoff status")
    }
    if (-not (Test-JsonNonNegativeInteger $latestLocalModelTemplate.data.summary.missing_required_fields_count)) {
        $localModelTemplateMismatches.Add("summary.missing_required_fields_count is not a non-negative JSON integer")
    }
    elseif ([int64]$latestLocalModelTemplate.data.summary.missing_required_fields_count -ne [int64]$localModelMissingFieldCount) {
        $localModelTemplateMismatches.Add("summary.missing_required_fields_count does not match missing_required_fields")
    }
    if ($localModelTemplateStatus -eq "manual_review_ready" -and $localModelMissingFieldCount -ne 0) {
        $localModelTemplateMismatches.Add("manual_review_ready local-model template still has missing required fields")
    }
    if ($evidenceTemplateStatus -ne "manual_clean_machine_local_model_evidence_required") {
        $localModelTemplateMismatches.Add("evidence_template.template_status is not manual_clean_machine_local_model_evidence_required")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.clean_machine_signoff)) {
        $localModelTemplateMismatches.Add("summary.clean_machine_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.local_model_install_pass)) {
        $localModelTemplateMismatches.Add("summary.local_model_install_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.local_model_start_pass)) {
        $localModelTemplateMismatches.Add("summary.local_model_start_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.local_model_pull_pass)) {
        $localModelTemplateMismatches.Add("summary.local_model_pull_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.local_model_task_smoke_pass)) {
        $localModelTemplateMismatches.Add("summary.local_model_task_smoke_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.real_install_start_pull_pass)) {
        $localModelTemplateMismatches.Add("summary.real_install_start_pull_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.template_is_clean_machine_pass)) {
        $localModelTemplateMismatches.Add("summary.template_is_clean_machine_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.dev_smoke_is_clean_machine_pass)) {
        $localModelTemplateMismatches.Add("summary.dev_smoke_is_clean_machine_pass is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.release_candidate_signoff)) {
        $localModelTemplateMismatches.Add("summary.release_candidate_signoff is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.starts_product_processes)) {
        $localModelTemplateMismatches.Add("readonly_scope.starts_product_processes is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.performs_network_requests)) {
        $localModelTemplateMismatches.Add("readonly_scope.performs_network_requests is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.installs_runtime)) {
        $localModelTemplateMismatches.Add("readonly_scope.installs_runtime is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.starts_runtime)) {
        $localModelTemplateMismatches.Add("readonly_scope.starts_runtime is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.pulls_models)) {
        $localModelTemplateMismatches.Add("readonly_scope.pulls_models is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.readonly_scope.runs_model_inference)) {
        $localModelTemplateMismatches.Add("readonly_scope.runs_model_inference is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.redaction.raw_logs_included)) {
        $localModelTemplateMismatches.Add("redaction.raw_logs_included is not false")
    }
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.redaction.secrets_or_tokens_read)) {
        $localModelTemplateMismatches.Add("redaction.secrets_or_tokens_read is not false")
    }
    if (-not (Test-JsonTrue $latestLocalModelTemplate.data.redaction.urls_redacted)) {
        $localModelTemplateMismatches.Add("redaction.urls_redacted is not true")
    }
    if ([string]$latestLocalModelTemplate.data.evidence_template.runtime.status -ne "unverified_by_this_helper") {
        $localModelTemplateMismatches.Add("evidence_template.runtime.status is not unverified_by_this_helper")
    }
    if ($localModelTemplateStatus -eq "manual_review_ready") {
        if (-not (Test-MeaningfulEvidenceValue ([string]$latestLocalModelTemplate.data.evidence_template.runtime.name))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing runtime.name")
        }
        if (-not (Test-MeaningfulEvidenceValue ([string]$latestLocalModelTemplate.data.evidence_template.runtime.version))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing runtime.version")
        }
    }
    if ([string]$latestLocalModelTemplate.data.evidence_template.model.status -ne "unverified_by_this_helper") {
        $localModelTemplateMismatches.Add("evidence_template.model.status is not unverified_by_this_helper")
    }
    if ($localModelTemplateStatus -eq "manual_review_ready") {
        if (-not (Test-MeaningfulEvidenceValue ([string]$latestLocalModelTemplate.data.evidence_template.model.name))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing model.name")
        }
        if (-not (Test-MeaningfulEvidenceValue ([string]$latestLocalModelTemplate.data.evidence_template.model.version))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing model.version")
        }
    }
    $artifactBuildProfile = $latestLocalModelTemplate.data.evidence_template.artifact_build_profile
    if ($null -eq $artifactBuildProfile) {
        $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile is missing")
    }
    else {
        if (-not (Test-LocalModelArtifactBuildProfileStatus ([string]$artifactBuildProfile.status))) {
            $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile.status is not an allowed fail-closed status")
        }
        if ($localModelTemplateStatus -eq "manual_review_ready" -and [string]$artifactBuildProfile.status -ne "recorded_unverified_by_this_helper") {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template artifact/build/profile status is not recorded")
        }
        if ([string]$artifactBuildProfile.artifact.status -ne "unverified_by_this_helper") {
            $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile.artifact.status is not unverified_by_this_helper")
        }
        if ($localModelTemplateStatus -eq "manual_review_ready" -and -not (Test-MeaningfulEvidenceValue ([string]$artifactBuildProfile.artifact.label))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing artifact label")
        }
        if ([string]$artifactBuildProfile.build.status -ne "unverified_by_this_helper") {
            $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile.build.status is not unverified_by_this_helper")
        }
        if ($localModelTemplateStatus -eq "manual_review_ready" -and -not (Test-MeaningfulEvidenceValue ([string]$artifactBuildProfile.build.identifier))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing build identifier")
        }
        if ([string]$artifactBuildProfile.profile.status -ne "unverified_by_this_helper") {
            $localModelTemplateMismatches.Add("evidence_template.artifact_build_profile.profile.status is not unverified_by_this_helper")
        }
        if ($localModelTemplateStatus -eq "manual_review_ready" -and -not (Test-MeaningfulEvidenceValue ([string]$artifactBuildProfile.profile.label))) {
            $localModelTemplateMismatches.Add("manual_review_ready local-model template is missing profile label")
        }
    }
    $cleanMachineRun = $latestLocalModelTemplate.data.evidence_template.clean_machine_run
    if ($null -eq $cleanMachineRun) {
        $localModelTemplateMismatches.Add("evidence_template.clean_machine_run is missing")
    }
    else {
        foreach ($stepName in @("install", "start", "pull", "task_smoke")) {
            $step = $cleanMachineRun.$stepName
            if ($null -eq $step) {
                $localModelTemplateMismatches.Add("evidence_template.clean_machine_run.$stepName is missing")
            }
            else {
                if (-not (Test-LocalModelStepStatus ([string]$step.status))) {
                    $localModelTemplateMismatches.Add("evidence_template.clean_machine_run.$stepName.status is not an allowed fail-closed status")
                }
                if (-not (Test-JsonFalse $step.pass_verified_by_this_helper)) {
                    $localModelTemplateMismatches.Add("evidence_template.clean_machine_run.$stepName.pass_verified_by_this_helper is not false")
                }
                if (-not (Test-JsonFalse $step.clean_machine_pass)) {
                    $localModelTemplateMismatches.Add("evidence_template.clean_machine_run.$stepName.clean_machine_pass is not false")
                }
                if ($localModelTemplateStatus -eq "manual_review_ready") {
                    if ([string]$step.status -ne "manual_outcome_recorded_unverified_by_this_helper") {
                        $localModelTemplateMismatches.Add("manual_review_ready local-model template $stepName status is not a recorded manual outcome")
                    }
                    if (-not (Test-MeaningfulEvidenceValue ([string]$step.outcome))) {
                        $localModelTemplateMismatches.Add("manual_review_ready local-model template $stepName outcome is missing")
                    }
                }
            }
        }
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "true local model install pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing true local model install pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "true local model start pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing true local model start pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "true local model pull pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing true local model pull pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "true local model task-smoke pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing true local model task-smoke pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "clean-machine local-model readiness")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing clean-machine local-model readiness")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "template/dev smoke clean-machine pass")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing template/dev smoke clean-machine pass")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "release-candidate sign-off")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing release-candidate sign-off")
    }
    if ($localModelTemplateMismatches.Count -gt 0) {
        $contractFailures.Add("latest local-model clean-machine helper artifact failed fail-closed validation")
    }
    $safeLocalModelMarker = if ($localModelTemplateMarker -eq "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS") { $localModelTemplateMarker } else { "invalid_redacted" }
    $safeLocalModelStatus = if ($localModelTemplateStatus -in @("manual_review_ready", "blocked_reason_recorded", "blocked_missing_required_fields")) { $localModelTemplateStatus } else { "invalid_redacted" }
    $safeArtifactBuildProfile = [ordered]@{
        status = Get-SafeLocalModelArtifactBuildProfileStatus ([string]$latestLocalModelTemplate.data.evidence_template.artifact_build_profile.status)
        artifact_under_test = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.artifact_build_profile.artifact.label)
        build_identifier = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.artifact_build_profile.build.identifier)
        profile_under_test = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.artifact_build_profile.profile.label)
    }
    $safeLocalModelRuntime = [ordered]@{
        name = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.runtime.name)
        version = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.runtime.version)
        status = if ([string]$latestLocalModelTemplate.data.evidence_template.runtime.status -eq "unverified_by_this_helper") { "unverified_by_this_helper" } else { "invalid_redacted" }
    }
    $safeLocalModelModel = [ordered]@{
        name = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.model.name)
        version = Redact-TextValue ([string]$latestLocalModelTemplate.data.evidence_template.model.version)
        status = if ([string]$latestLocalModelTemplate.data.evidence_template.model.status -eq "unverified_by_this_helper") { "unverified_by_this_helper" } else { "invalid_redacted" }
    }
    $safeCleanMachineRun = [ordered]@{
        install = New-SafeLocalModelStepSummary $latestLocalModelTemplate.data.evidence_template.clean_machine_run.install
        start = New-SafeLocalModelStepSummary $latestLocalModelTemplate.data.evidence_template.clean_machine_run.start
        pull = New-SafeLocalModelStepSummary $latestLocalModelTemplate.data.evidence_template.clean_machine_run.pull
        task_smoke = New-SafeLocalModelStepSummary $latestLocalModelTemplate.data.evidence_template.clean_machine_run.task_smoke
    }
    [ordered]@{
        found = $true
        path = $latestLocalModelTemplate.path
        last_write_utc = $latestLocalModelTemplate.last_write_utc
        marker = $safeLocalModelMarker
        source_contract_status = if ($localModelTemplateMismatches.Count -eq 0) { "valid_not_signoff_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($localModelTemplateMismatches)
        template_status = $safeLocalModelStatus
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        real_install_start_pull_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        release_candidate_signoff = $false
        artifact_build_profile = $safeArtifactBuildProfile
        runtime = $safeLocalModelRuntime
        model = $safeLocalModelModel
        clean_machine_run = $safeCleanMachineRun
        missing_required_fields_count = Get-ArrayCount $latestLocalModelTemplate.data.summary.missing_required_fields
        blocked_reason_count = Get-ArrayCount $latestLocalModelTemplate.data.evidence_template.blocked_reason_redacted
        observed_artifact_count = Get-ArrayCount $latestLocalModelTemplate.data.evidence_template.observed_artifacts_redacted
    }
}
elseif ($latestLocalModelTemplate.found) {
    $contractFailures.Add("latest local-model clean-machine helper artifact could not be parsed")
    [ordered]@{
        found = $true
        path = $latestLocalModelTemplate.path
        last_write_utc = $latestLocalModelTemplate.last_write_utc
        parse_error = $latestLocalModelTemplate.error
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        real_install_start_pull_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        release_candidate_signoff = $false
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $localModelCleanMachineEvidenceRootPath
        template_status = "not_collected_by_this_packet"
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        real_install_start_pull_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        release_candidate_signoff = $false
    }
}

    return [ordered]@{
        ollamaCounts = $ollamaCounts
        ollamaContractCount = $ollamaContractCount
        localModelTemplateContract = $localModelTemplateContract
        localModelTemplateLatestSummary = $localModelTemplateLatestSummary
    }
}

