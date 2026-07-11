[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$MobilePreflightEvidenceRoot = "",
    [string]$AndroidRealDeviceEvidenceRoot = "",
    [string]$AndroidReleaseGateEvidenceRoot = "",
    [string]$QaEvidenceRoot = "",
    [string]$DiagnosticsReviewEvidenceRoot = "",
    [string]$ResultQualityReviewEvidenceRoot = "",
    [string]$LocalModelCleanMachineEvidenceRoot = "",
    [string]$RcHandoffEvidenceRoot = "",
    [string]$PortableFirstScreenEvidenceRoot = ""
)

$ErrorActionPreference = "Stop"
try {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = $utf8NoBom
    [Console]::InputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
}
catch {
}

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
. (Join-Path $PSScriptRoot "lib\release_evidence_common.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_markdown.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_contracts.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_portable.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_mobile.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_android.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_local_model.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_diagnostics.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_result_quality.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_rc.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_settings.ps1")
. (Join-Path $PSScriptRoot "lib\release_evidence_readiness.ps1")
$evidenceRootPath = Resolve-InputPath $EvidenceRoot ".tmp\release-evidence-packet"
$mobileEvidenceRootPath = Resolve-InputPath $MobilePreflightEvidenceRoot ".tmp\mobile-lan-wss-preflight"
$androidRealDeviceEvidenceRootPath = Resolve-IsolatedOptionalInputPath $AndroidRealDeviceEvidenceRoot ".tmp\android-real-device-evidence-template" "empty-android-real-device-evidence-template"
$androidReleaseGateEvidenceRootPath = Resolve-IsolatedOptionalInputPath $AndroidReleaseGateEvidenceRoot ".tmp\android-release-gate" "empty-android-release-gate"
$qaEvidenceRootPath = Resolve-InputPath $QaEvidenceRoot ".tmp\qa-evidence"
$diagnosticsReviewEvidenceRootPath = Resolve-InputPath $DiagnosticsReviewEvidenceRoot ".tmp\diagnostics-external-review"
$resultQualityReviewEvidenceRootPath = Resolve-InputPath $ResultQualityReviewEvidenceRoot ".tmp\result-quality-review"
$localModelCleanMachineEvidenceRootPath = Resolve-InputPath $LocalModelCleanMachineEvidenceRoot ".tmp\local-model-clean-machine-evidence"
$rcHandoffEvidenceRootPath = Resolve-InputPath $RcHandoffEvidenceRoot ".tmp\rc-handoff-template"
$portableFirstScreenEvidenceRootPath = Resolve-InputPath $PortableFirstScreenEvidenceRoot ".tmp\portable-first-screen-smoke"
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $evidenceRootPath "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$jsonPath = Join-Path $runRoot "release-evidence-packet.redacted.json"
$markdownPath = Join-Path $runRoot "release-evidence-packet.redacted.md"

function Get-ReleasePackageVersion {
    $packagePath = Join-Path $resolvedRoot "package.json"
    if (-not (Test-Path -LiteralPath $packagePath)) {
        return "unknown"
    }

    try {
        $package = Get-Content -LiteralPath $packagePath -Raw | ConvertFrom-Json
        if (-not [string]::IsNullOrWhiteSpace([string]$package.version)) {
            $version = ([string]$package.version).Trim()
            if ($version.StartsWith("v")) {
                return $version
            }
            return "v$version"
        }
    }
    catch {
    }

    return "unknown"
}

$contractFailures = New-Object System.Collections.Generic.List[string]

$sourceContracts = Get-ReleaseEvidenceSourceContracts -contractFailures $contractFailures
$mobileContract = $sourceContracts.mobileContract
$androidReleaseGateContract = $sourceContracts.androidReleaseGateContract
$androidRealDeviceTemplateContract = $sourceContracts.androidRealDeviceTemplateContract
$portableContract = $sourceContracts.portableContract
$mobileRemoteInputContracts = @($sourceContracts.mobileRemoteInputContracts)
$portableLatestSummary = New-PortableFirstScreenLatestSummary -portableFirstScreenEvidenceRootPath $portableFirstScreenEvidenceRootPath -contractFailures $contractFailures
$mobileLatestSummary = New-MobileLanWssLatestSummary -mobileEvidenceRootPath $mobileEvidenceRootPath -contractFailures $contractFailures
$androidRealDeviceTemplateLatestSummary = New-AndroidRealDeviceTemplateLatestSummary -androidRealDeviceEvidenceRootPath $androidRealDeviceEvidenceRootPath -contractFailures $contractFailures
$androidReleaseGateLatestSummary = New-AndroidReleaseGateLatestSummary -androidReleaseGateEvidenceRootPath $androidReleaseGateEvidenceRootPath -contractFailures $contractFailures
$localModelSummary = New-LocalModelEvidenceSummary -localModelCleanMachineEvidenceRootPath $localModelCleanMachineEvidenceRootPath -contractFailures $contractFailures
$ollamaCounts = @($localModelSummary.ollamaCounts)
$ollamaContractCount = $localModelSummary.ollamaContractCount
$localModelTemplateContract = $localModelSummary.localModelTemplateContract
$localModelTemplateLatestSummary = $localModelSummary.localModelTemplateLatestSummary
$diagnosticsSummary = New-DiagnosticsExternalReviewEvidenceSummary -diagnosticsReviewEvidenceRootPath $diagnosticsReviewEvidenceRootPath -contractFailures $contractFailures
$diagnosticsContract = $diagnosticsSummary.diagnosticsContract
$diagnosticsReviewLatestSummary = $diagnosticsSummary.diagnosticsReviewLatestSummary
$resultQualitySummary = New-ResultQualityReviewEvidenceSummary -resultQualityReviewEvidenceRootPath $resultQualityReviewEvidenceRootPath -contractFailures $contractFailures
$resultQualityReviewContract = $resultQualitySummary.resultQualityReviewContract
$resultQualityReviewLatestSummary = $resultQualitySummary.resultQualityReviewLatestSummary
$rcHandoffSummary = New-RcHandoffEvidenceSummary -rcHandoffEvidenceRootPath $rcHandoffEvidenceRootPath -contractFailures $contractFailures
$rcHandoffContract = $rcHandoffSummary.rcHandoffContract
$rcHandoffLatestSummary = $rcHandoffSummary.rcHandoffLatestSummary
$settingsSummary = New-SettingsLocalModelEvidenceSummary -qaEvidenceRootPath $qaEvidenceRootPath -contractFailures $contractFailures
$settingsContract = $settingsSummary.settingsContract
$settingsArtifactNames = @($settingsSummary.settingsArtifactNames)
$settingsArtifacts = @($settingsSummary.settingsArtifacts)
$settingsArtifactsPresent = $settingsSummary.settingsArtifactsPresent
$releaseReadinessBlockers = New-ReleaseReadinessBlockers -androidReleaseGateLatestSummary $androidReleaseGateLatestSummary -diagnosticsReviewLatestSummary $diagnosticsReviewLatestSummary
$currentReleaseEvidencePath = Join-Path $resolvedRoot "docs\release\current-release-evidence.md"
$packet = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    generated_by = "scripts/collect_release_evidence_packet.ps1"
    candidate_context = [ordered]@{
        release_version = Get-ReleasePackageVersion
        current_release_evidence = (Get-DisplayPath $currentReleaseEvidencePath).Replace("\", "/")
        current_release_evidence_present = Test-Path -LiteralPath $currentReleaseEvidencePath
        candidate_commit_source = "docs/release/current-release-evidence.md Commit SHA"
        build_identifier_source = "docs/release/current-release-evidence.md Build identifier or manual RC handoff build id"
        strict_readiness_cross_check = "dashboard Candidate commit and current-release evidence Commit SHA must match checked-out HEAD"
    }
    outputs = [ordered]@{
        redacted_json = Get-DisplayPath $jsonPath
        redacted_markdown = Get-DisplayPath $markdownPath
    }
    readonly_scope = [ordered]@{
        starts_product_processes = $false
        performs_network_requests = $false
        changes_backend_product_logic = $false
        changes_desktop_ui = $false
        changes_mobile_app = $false
        writes_only_packet_summary_artifacts = $true
    }
    redaction = [ordered]@{
        path_policy = "workspace-relative paths or file labels only"
        raw_logs_included = $false
        source_artifacts_read_for_summary = $true
        secrets_or_tokens_emitted = $false
        mobile_hosts = "uses existing redacted labels only"
    }
    summary = [ordered]@{
        automated_evidence_items = 12
        indexed_evidence_buckets = 12
        evidence_count_is_not_acceptance_count = $true
        source_contract_failures = $contractFailures.Count
        packet_is_pass = $false
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        local_model_task_smoke_pass = $false
        template_is_clean_machine_pass = $false
        dev_smoke_is_clean_machine_pass = $false
        real_device_signoff = $false
        release_candidate_signoff = $false
        agent_task_completion_signoff = $false
        result_quality_signoff = $false
        diagnostics_public_safe = $false
        diagnostics_external_share_machine_chain_ready = [bool]$diagnosticsReviewLatestSummary.manual_content_review_only_remaining
        diagnostics_external_share_manual_content_review_pending = $true
        release_ready = $false
        claimable_release_signoff = $false
        release_readiness_blocker_count = $releaseReadinessBlockers.Count
        portable_natural_language_scope = "submission_plus_read_only_routing_evidence_only"
        manual_content_review_required = $true
        packet_status = if ($contractFailures.Count -eq 0) { "redacted_partial_evidence_summary" } else { "source_contract_failure" }
    }
    evidence = [ordered]@{
        mobile_lan_wss_preflight = [ordered]@{
            status = if ($mobileContract.required_markers_present) { "entry_available" } else { "source_contract_missing" }
            source_contract = $mobileContract
            latest_redacted_summary = $mobileLatestSummary
            automated_scope = "static preflight contract plus latest redacted summary if present"
            not_signoff = @(
                "not a phone/emulator run",
                "not camera or QR scan evidence",
                "not an actual WSS connection",
                "not Android/emulator certificate trust evidence",
                "not real-device pass evidence"
            )
        }
        android_release_gate = [ordered]@{
            status = if ($androidReleaseGateContract.required_markers_present) { "entry_available" } else { "source_contract_missing" }
            source_contract = $androidReleaseGateContract
            latest_redacted_summary = $androidReleaseGateLatestSummary
            expected_preflight_status = "preflight_ready_not_release"
            expected_strict_status = "passed"
            expected_packet_creates_apk_or_real_device_pass = $false
            automated_scope = "source/config preflight plus strict gate result when APK and real-device evidence are supplied"
            not_signoff = @(
                "preflight is not an APK build",
                "preflight is not an install on Android",
                "preflight is not phone/emulator WSS evidence",
                "strict gate remains blocked without installable APK and reviewed real-device evidence"
            )
        }
        android_real_device_evidence_template = [ordered]@{
            status = if ($androidRealDeviceTemplateContract.required_markers_present) { "fail_closed_template_contract_present" } else { "source_contract_missing" }
            source_contract = $androidRealDeviceTemplateContract
            latest_redacted_template = $androidRealDeviceTemplateLatestSummary
            automated_scope = "template/source contract and latest fail-closed redacted template if present"
            expected_template_status = "manual_real_device_evidence_required"
            expected_real_device_result = "uncollected"
            expected_pass_claim_allowed = $false
            not_signoff = @(
                "template is not a phone/emulator run",
                "template is not camera QR evidence",
                "template is not HTTPS/WSS or certificate-trust evidence",
                "template is not remote screen/input/revoke/expiry evidence",
                "template is not a real-device pass until reviewed artifacts fill every required check"
            )
        }
        mobile_remote_input_active_grant_contract = [ordered]@{
            status = if (($mobileRemoteInputContracts | Where-Object { -not $_.required_markers_present }).Count -eq 0) { "fail_closed_source_contract_present" } else { "source_contract_missing" }
            source_contracts = $mobileRemoteInputContracts
            automated_scope = "static source contract markers in mobile UI/client/smoke sources"
            required_markers = @(
                "assertRemoteInputApprovalMatchesSession",
                "remoteInputApprovalMatchesActiveGrant",
                "client-side remote-input binding failures must not reach the smoke server"
            )
            verify_command = "npm --prefix mobile run smoke:remote-input-grant"
            latest_execution_status = "not_run_by_this_packet"
            not_signoff = @(
                "not_signoff=source/client contract only, not live device/WSS",
                "not evidence that the smoke command was executed by this packet",
                "not a real phone/emulator run",
                "not proof of a live desktop-to-mobile remote input session",
                "not backend TestClient, desktop smoke, packaged, or clean-machine evidence by itself",
                "not actual WSS network evidence",
                "not release-candidate sign-off"
            )
        }
        portable_first_screen_smoke = [ordered]@{
            status = if ($portableContract.required_markers_present) { "limited_evidence_contract_present" } else { "source_contract_missing" }
            source_contract = $portableContract
            latest_redacted_status_log = $portableLatestSummary
            automated_scope = "latest redacted portable.status.log summary if present"
            verify_command = "npm run smoke:portable-first-screen"
            not_signoff = @(
                "not clean-machine release-candidate install validation",
                "not completed task-result sign-off",
                "not release-candidate sign-off",
                "not full natural-language agent task completion",
                "not platform distribution evidence"
            )
        }
        ollama_local_model_contracts = [ordered]@{
            status = if ($ollamaContractCount -gt 0) { "contract_count_available" } else { "contract_count_missing" }
            contract_count = $ollamaContractCount
            files = $ollamaCounts
            verify_command = "python -m pytest backend/tests/test_ollama_service.py backend/tests/test_ollama_install_endpoint.py -q"
            latest_execution_status = "not_run_by_this_packet"
            not_signoff = @(
                "not clean-machine local model install evidence",
                "not packaged-profile local model start evidence",
                "not proof that a model was pulled or listed by a real Ollama service"
            )
        }
        local_model_clean_machine_template = [ordered]@{
            status = if ($localModelTemplateContract.required_markers_present) { "manual_clean_machine_template_contract_present" } else { "source_contract_missing" }
            source_contract = $localModelTemplateContract
            latest_redacted_clean_machine_template = $localModelTemplateLatestSummary
            expected_artifact = "local-model-clean-machine-evidence.redacted.json"
            validation_failure_message = "latest local-model clean-machine helper artifact failed fail-closed validation"
            expected_marker = "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
            expected_clean_machine_signoff = $false
            expected_install_start_pull_pass = $false
            expected_task_smoke_pass = $false
            verify_command = "python -m pytest backend/tests/test_start_app_script.py -q"
            not_signoff = @(
                "not true local model install pass",
                "not true local model start pass",
                "not true local model pull pass",
                "not true local model task-smoke pass",
                "not clean-machine local-model readiness",
                "template/dev smoke must not be recorded as clean-machine pass",
                "not release-candidate sign-off"
            )
        }
        diagnostics_external_review = [ordered]@{
            status = if ($diagnosticsContract.required_markers_present) { "manual_review_required_contract_present" } else { "source_contract_missing" }
            source_contract = $diagnosticsContract
            latest_redacted_review_packet = $diagnosticsReviewLatestSummary
            expected_artifact = "diagnostics-external-review.redacted.json"
            expected_external_review_status = "manual_review_required"
            expected_required_before_external_sharing = $true
            expected_public_safe = $false
            expected_claim_allowed = $false
            expected_actual_package_content_review_completed = $false
            expected_external_sharing_blocked = $true
            expected_separate_human_content_review_required = $true
            machine_chain_status = $diagnosticsReviewLatestSummary.machine_chain_status
            manual_content_review_only_remaining = [bool]$diagnosticsReviewLatestSummary.manual_content_review_only_remaining
            reviewed_evidence_validator = "scripts/verify_diagnostics_external_reviewed_evidence.py"
            reviewed_evidence_artifact_type = "diagnostics-external-review-evidence-reviewed"
            strict_pipeline_stage = "diagnostics-evidence"
            verify_command = "python -m pytest backend/tests/test_system_diagnostics.py -q"
            not_signoff = @(
                "not external public-safety approval",
                "not permission to share diagnostics outside trusted support",
                "not clean-machine diagnostics sign-off",
                "not a human content review sign-off"
            )
        }
        result_quality_review = [ordered]@{
            status = if ($resultQualityReviewContract.required_markers_present) { "manual_review_required_contract_present" } else { "source_contract_missing" }
            source_contract = $resultQualityReviewContract
            latest_redacted_review_packet = $resultQualityReviewLatestSummary
            expected_artifact = "result-quality-review.redacted.json"
            expected_marker = "NOT_RESULT_QUALITY_SIGNOFF"
            expected_result_quality_signoff = $false
            result_quality_claim_blocked = $true
            separate_human_signoff_required = $true
            expected_claim_allowed = $false
            expected_completed_result_evidence = $false
            validation_failure_messages = @(
                "summary.review_fields_complete does not match missing/issue/status state",
                "summary.external_sharing_blocked is not true",
                "summary.separate_human_content_review_required is not true",
                "claim_controls.external_sharing_blocked is not true",
                "claim_controls.separate_human_content_review_required is not true",
                "latest result-quality review helper artifact failed fail-closed validation"
            )
            verify_command = "python -m pytest backend/tests/test_result_quality_review_packet.py -q"
            not_signoff = @(
                "not completed-result evidence",
                "not natural-language result-quality sign-off",
                "not Task Workspace sign-off",
                "not release-candidate sign-off",
                "not release sign-off"
            )
        }
        rc_handoff_template = [ordered]@{
            status = if ($rcHandoffContract.required_markers_present) { "manual_rc_handoff_contract_present" } else { "source_contract_missing" }
            source_contract = $rcHandoffContract
            latest_redacted_handoff_template = $rcHandoffLatestSummary
            expected_artifact = "rc-handoff-template.redacted.json"
            expected_marker = "NOT_RELEASE_CANDIDATE_SIGNOFF"
            expected_release_candidate_signoff = $false
            expected_claim_allowed = $false
            expected_gate_commands_run_by_this_helper = $false
            validation_failure_messages = @(
                "summary.release_candidate_signoff is not false",
                "summary.gate_commands_run_by_this_helper is not false",
                "signoff_controls.must_not_tag_publish_or_announce is not true",
                "latest RC handoff helper artifact failed fail-closed validation"
            )
            verify_command = "python -m pytest backend/tests/test_start_app_script.py -q"
            not_signoff = @(
                "not release-candidate pass",
                "not release-candidate sign-off",
                "not release sign-off",
                "not proof that release gates were run",
                "not permission to tag, publish, announce, or ship",
                "not waiver approval",
                "not manual P1 review approval"
            )
        }
        settings_local_model_smoke = [ordered]@{
            status = if ($settingsContract.required_markers_present -and $settingsArtifactsPresent -eq $settingsArtifactNames.Count) { "source_contract_and_artifacts_present" } elseif ($settingsContract.required_markers_present) { "source_contract_present_artifacts_incomplete" } else { "source_contract_missing" }
            source_contract = $settingsContract
            expected_artifact_count = $settingsArtifactNames.Count
            present_artifact_count = $settingsArtifactsPresent
            artifacts = $settingsArtifacts
            verify_command = "npm --prefix desktop run smoke:settings-local-model"
            latest_execution_status = "not_run_by_this_packet"
            not_signoff = @(
                "not clean-machine local-model readiness",
                "not packaged Settings evidence",
                "not release-candidate layout sign-off",
                "not true local model install/start/pull evidence"
            )
        }
    }
    not_clean_machine_or_signoff = @(
        "This packet summarizes automatically checkable source contracts and existing redacted artifacts only.",
        "It does not create clean-machine install/start/pull local-model evidence.",
        "It does not create clean-machine local-model task-smoke pass evidence.",
        "It does not turn the local-model template or Settings dev smoke into clean-machine pass evidence.",
        "It does not create real phone/emulator camera/QR/WSS/certificate-trust evidence.",
        "It does not create installable Android APK pass or real-device Android remote-control pass; Android template/gate entries are indexed redacted evidence only.",
        "It does not create natural-language result-quality sign-off; result_quality_signoff remains false pending separate human sign-off.",
        "It does not make diagnostics packages public-safe; public_safe remains false pending manual external review.",
        "It is not release-candidate sign-off without the release gate command results, candidate id, artifact paths, and manual P1 sign-off."
    )
    rc_handoff_requirements = [ordered]@{
        status = "manual_rc_handoff_required"
        release_candidate_signoff = $false
        packet_is_rc_signoff = $false
        must_not_claim = "release-candidate pass"
        required_before_rc_signoff = @(
            "candidate commit or build id",
            "platform and packaged artifact paths or redacted artifact labels",
            "exact release gate commands and full exit status",
            "strict-state-machine source used for the release gate",
            "manual P1 checks with owner and timestamp",
            "waivers with owner, reason, expiry condition, and follow-up task",
            "residual risks"
        )
        missing_by_default = @(
            "candidate commit or build id",
            "platform and packaged artifact paths or redacted artifact labels",
            "exact release gate commands and full exit status",
            "strict-state-machine source used for the release gate",
            "manual P1 checks",
            "waivers",
            "residual risks"
        )
        must_not_be_recorded_as = @(
            "release-candidate pass",
            "release sign-off",
            "clean-machine pass",
            "real-device pass",
            "public-safe diagnostics approval",
            "completed task-result sign-off"
        )
        beginner_instruction = "Use this packet as a redacted checklist only; do not tag, publish, or announce an RC until a separate handoff fills every required field."
    }
    release_readiness_blockers = $releaseReadinessBlockers
    release_readiness_markdown_contract = [ordered]@{
        heading = "## Release Readiness Blockers"
        count_line = 'blocker_count=$($packet.summary.release_readiness_blocker_count)'
    }
    natural_language_completion_evidence = [ordered]@{
        markdown_field = "completion_evidence\.level"
        completed_result_without_signoff_expression = '$naturalLanguageCompletionLevel -eq "completed_result" -and $naturalLanguageResultVerified -and -not $naturalLanguageSignoff'
        validation_failure_messages = @(
            "natural-language pass line reports result_verified without completed_result level",
            "natural-language pass line must not report completion_evidence signoff"
        )
    }
    release_readiness_blocker_labels = @(
        "missing_real_device_artifacts",
        "missing_result_quality_signoff"
    )
    next_manual_evidence_needed = @(
        "Clean-machine or packaged-profile local model install/start/pull/task-smoke evidence when local/offline model readiness is claimed.",
        "Real phone/emulator camera or QR pairing path, actual WSS connection, and explicit device certificate trust evidence when mobile LAN/WSS readiness is claimed.",
        "Installable Android QA APK path/hash plus filled reviewed Android real-device evidence JSON and strict Android release gate evidence before claiming Android app or Android remote-control readiness.",
        "Actual natural-language result-quality human sign-off after reviewing the user-visible result, source/artifact labels, and next-step actionability.",
        "Manual diagnostics package review before any external sharing.",
        "Release-candidate artifact verification and manual P1 sign-off before RC approval."
    )
}

$markdown = New-ReleaseEvidencePacketMarkdown -Packet $packet -SettingsArtifactsPresent $settingsArtifactsPresent -SettingsArtifactCount $settingsArtifactNames.Count

$packet | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $jsonPath -Encoding utf8
$markdown | Set-Content -LiteralPath $markdownPath -Encoding utf8

Write-Host "Release evidence packet summary"
Write-Host "Redacted JSON: $($packet.outputs.redacted_json)"
Write-Host "Redacted Markdown: $($packet.outputs.redacted_markdown)"
Write-Host ""
Write-Host $markdown

if ($contractFailures.Count -gt 0) {
    Write-Host ""
    Write-Host "[blocked] Release evidence packet source contracts need attention:" -ForegroundColor Red
    foreach ($failure in $contractFailures) {
        Write-Host " - $failure" -ForegroundColor Red
    }
    exit 1
}

exit 0
