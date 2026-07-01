function New-PortableFirstScreenLatestSummary {
    param(
        [Parameter(Mandatory = $true)]$portableFirstScreenEvidenceRootPath,
        [Parameter(Mandatory = $true)]$contractFailures
    )

$latestPortableStatus = Find-LatestTextArtifact $portableFirstScreenEvidenceRootPath "portable.status.log"
$portableLatestSummary = if ($latestPortableStatus.found -and -not [string]::IsNullOrWhiteSpace($latestPortableStatus.text)) {
    $portableText = [string]$latestPortableStatus.text
    $readOnlyLine = Get-FirstLogLine $portableText "\[pass\]\s+portable renderer DOM read-only task evidence passed:"
    $naturalLanguageLine = Get-FirstLogLine $portableText "\[pass\]\s+portable renderer DOM natural-language read-only task evidence passed:"
    $firstScreenLine = Get-FirstLogLine $portableText "\[pass\]\s+portable first-screen/read-only diagnostics smoke passed:"
    $readOnlyPass = -not [string]::IsNullOrWhiteSpace($readOnlyLine)
    $naturalLanguagePass = -not [string]::IsNullOrWhiteSpace($naturalLanguageLine)
    $firstScreenPass = -not [string]::IsNullOrWhiteSpace($firstScreenLine)
    $unsupported = $portableText -match "\[unsupported\]"
    $failed = $portableText -match "\[(fail|blocked)\]"
    $readOnlyTasksValue = Get-RegexFirstGroup $readOnlyLine "(?<![A-Za-z0-9_])tasks[=:]\s*(\d+)"
    $readOnlyRunsValue = Get-RegexFirstGroup $readOnlyLine "(?<![A-Za-z0-9_])runs[=:]\s*(\d+)"
    $readOnlyChatValue = Get-RegexFirstGroup $readOnlyLine "chat messages[=:]\s*(\d+)"
    $readOnlyDiagnosticPackagesValue = Get-RegexFirstGroup $readOnlyLine "diagnostic-packages[=:]\s*(\d+)"
    $naturalLanguageRunsValue = Get-RegexFirstGroup $naturalLanguageLine "(?<![A-Za-z0-9_])runs[=:]\s*(\d+)"
    $naturalLanguageTasksValue = Get-RegexFirstGroup $naturalLanguageLine "(?<![A-Za-z0-9_])tasks[=:]\s*(\d+)"
    $naturalLanguageRelatedTasksValue = Get-RegexFirstGroup $naturalLanguageLine "relatedTasks[=:]\s*(\d+)"
    $naturalLanguageRelatedRunsValue = Get-RegexFirstGroup $naturalLanguageLine "relatedRuns[=:]\s*(\d+)"
    $naturalLanguageChatValue = Get-RegexFirstGroup $naturalLanguageLine "chat messages[=:]\s*(\d+)"
    $naturalLanguageDiagnosticPackagesValue = Get-RegexFirstGroup $naturalLanguageLine "diagnostic-packages[=:]\s*(\d+)"
    $naturalLanguageCompletionLevelValue = Get-RegexFirstGroup $naturalLanguageLine "(?i)completion_evidence\.level[=:]\s*([A-Za-z0-9_\-]+)"
    $naturalLanguageResultVerifiedValue = Get-RegexFirstGroup $naturalLanguageLine "(?i)result_verified[=:]\s*(true|false)"
    $naturalLanguageSignoffValue = Get-RegexFirstGroup $naturalLanguageLine "(?i)(?<![A-Za-z0-9_])signoff[=:]\s*(true|false)"
    $readOnlyTasks = ConvertTo-IntOrZero $readOnlyTasksValue
    $readOnlyRuns = ConvertTo-IntOrZero $readOnlyRunsValue
    $readOnlyChatMessages = ConvertTo-IntOrZero $readOnlyChatValue
    $readOnlyDiagnosticPackages = ConvertTo-IntOrZero $readOnlyDiagnosticPackagesValue
    $naturalLanguageTasks = ConvertTo-IntOrZero $naturalLanguageTasksValue
    $naturalLanguageRuns = ConvertTo-IntOrZero $naturalLanguageRunsValue
    $naturalLanguageRelatedTasks = ConvertTo-IntOrZero $naturalLanguageRelatedTasksValue
    $naturalLanguageRelatedRuns = ConvertTo-IntOrZero $naturalLanguageRelatedRunsValue
    $naturalLanguageChatMessages = ConvertTo-IntOrZero $naturalLanguageChatValue
    $naturalLanguageDiagnosticPackages = ConvertTo-IntOrZero $naturalLanguageDiagnosticPackagesValue
    $allowedCompletionLevels = @("submission", "task_created", "visible_progress", "completed_result", "safe_failure", "not_collected")
    $naturalLanguageCompletionLevel = if ([string]::IsNullOrWhiteSpace($naturalLanguageCompletionLevelValue)) {
        "not_collected"
    }
    elseif ($naturalLanguageCompletionLevelValue -in $allowedCompletionLevels) {
        $naturalLanguageCompletionLevelValue
    }
    else {
        "invalid"
    }
    $naturalLanguageResultVerified = $naturalLanguageResultVerifiedValue -match "^(?i:true)$"
    $naturalLanguageSignoff = $naturalLanguageSignoffValue -match "^(?i:true)$"
    $postEndpoint = if ($naturalLanguageLine -match "observed expected POST /api/runs") { "/api/runs" } elseif ($naturalLanguageLine -match "observed expected POST /api/chat") { "/api/chat" } else { "" }
    $readOnlyNoWrites = $readOnlyPass -and $readOnlyTasksValue -ne "" -and $readOnlyRunsValue -ne "" -and $readOnlyChatValue -ne "" -and $readOnlyDiagnosticPackagesValue -ne "" -and $readOnlyTasks -eq 0 -and $readOnlyRuns -eq 0 -and $readOnlyChatMessages -eq 0 -and $readOnlyDiagnosticPackages -eq 0
    $naturalLanguageSubmissionSemanticValid = $naturalLanguageLine -match "submitted natural-language prompt through packaged command dock"
    $naturalLanguageTaskSemanticValid = $naturalLanguageLine -match "natural-language prompt created read-only/system diagnostics task"
    $naturalLanguageRunSemanticValid = $naturalLanguageLine -match "natural-language prompt created read-only/system diagnostics run"
    $naturalLanguageSemanticValid = $naturalLanguageTaskSemanticValid -or $naturalLanguageRunSemanticValid
    $naturalLanguageCoreCountsPresent = $naturalLanguageTasksValue -ne "" -and $naturalLanguageRelatedTasksValue -ne "" -and $naturalLanguageRunsValue -ne "" -and $naturalLanguageRelatedRunsValue -ne ""
    $naturalLanguageTaskCountValid = $naturalLanguageTaskSemanticValid -and $naturalLanguageTasksValue -ne "" -and $naturalLanguageTasks -ge 1 -and $naturalLanguageRelatedTasksValue -ne "" -and $naturalLanguageRelatedTasks -ge 1
    $naturalLanguageRunCountValid = $naturalLanguageRunSemanticValid -and $naturalLanguageRunsValue -ne "" -and $naturalLanguageRuns -ge 1 -and $naturalLanguageRelatedRunsValue -ne "" -and $naturalLanguageRelatedRuns -ge 1
    $naturalLanguageRelatedEvidenceValid = $naturalLanguageTaskCountValid -or $naturalLanguageRunCountValid
    $naturalLanguageCountsValid = $naturalLanguagePass -and $naturalLanguageSubmissionSemanticValid -and $postEndpoint -ne "" -and $naturalLanguageCoreCountsPresent -and $naturalLanguageRelatedEvidenceValid -and $naturalLanguageChatValue -ne "" -and $naturalLanguageDiagnosticPackagesValue -ne "" -and $naturalLanguageChatMessages -eq 0 -and $naturalLanguageDiagnosticPackages -eq 0
    $naturalLanguageCompletedResultEvidenceCandidate = [bool]($naturalLanguageCountsValid -and $naturalLanguageCompletionLevel -eq "completed_result" -and $naturalLanguageResultVerified -and -not $naturalLanguageSignoff)
    $portableLogMismatches = New-Object System.Collections.Generic.List[string]
    if (($readOnlyPass -or $naturalLanguagePass -or $firstScreenPass) -and $failed) {
        $portableLogMismatches.Add("portable status log contains pass and fail/blocked lines")
    }
    if ($readOnlyPass -and -not $readOnlyNoWrites) {
        $portableLogMismatches.Add("read-only pass line does not prove zero task/run/chat/export side effects")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageCountsValid) {
        $portableLogMismatches.Add("natural-language pass line does not prove POST plus read-only task/run evidence")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageSubmissionSemanticValid) {
        $portableLogMismatches.Add("natural-language pass line does not prove packaged command-dock submission semantics")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageSemanticValid) {
        $portableLogMismatches.Add("natural-language pass line does not prove read-only/system diagnostics task or run semantics")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageCoreCountsPresent) {
        $portableLogMismatches.Add("natural-language pass line is missing required task/run relation counts")
    }
    if ($naturalLanguagePass -and -not $naturalLanguageRelatedEvidenceValid) {
        $portableLogMismatches.Add("natural-language pass line does not prove related read-only/system diagnostics task or run evidence")
    }
    if ($naturalLanguagePass -and $naturalLanguageCompletionLevel -eq "invalid") {
        $portableLogMismatches.Add("natural-language pass line has invalid completion_evidence.level")
    }
    if ($naturalLanguagePass -and $naturalLanguageResultVerified -and $naturalLanguageCompletionLevel -ne "completed_result") {
        $portableLogMismatches.Add("natural-language pass line reports result_verified without completed_result level")
    }
    if ($naturalLanguagePass -and $naturalLanguageSignoff) {
        $portableLogMismatches.Add("natural-language pass line must not report completion_evidence signoff")
    }
    if (($readOnlyPass -or $naturalLanguagePass) -and -not $firstScreenPass) {
        $portableLogMismatches.Add("portable status log is missing final first-screen pass line")
    }
    if ($portableLogMismatches.Count -gt 0) {
        $contractFailures.Add("latest portable first-screen status log failed limited-evidence validation")
    }
    $naturalLanguageCompletedResultEvidence = [bool]($naturalLanguageCompletedResultEvidenceCandidate -and $portableLogMismatches.Count -eq 0)
    [ordered]@{
        found = $true
        path = $latestPortableStatus.path
        last_write_utc = $latestPortableStatus.last_write_utc
        source_contract_status = if ($portableLogMismatches.Count -gt 0) { "source_contract_mismatch" } elseif ($readOnlyPass -and $firstScreenPass -and $readOnlyNoWrites) { "valid_limited_evidence_log" } else { "limited_or_incomplete_evidence_log" }
        mismatch_reasons = @($portableLogMismatches)
        first_screen_read_only_pass = [bool]($readOnlyPass -and $firstScreenPass -and $readOnlyNoWrites -and $portableLogMismatches.Count -eq 0)
        renderer_dom_read_only_evidence = if ($readOnlyPass) { "passed" } elseif ($unsupported) { "unsupported" } else { "not_observed" }
        natural_language_submission_evidence = if ($naturalLanguagePass -and $naturalLanguageCountsValid -and $portableLogMismatches.Count -eq 0) { "packaged_command_dock_submission_plus_read_only_task_evidence" } elseif ($naturalLanguagePass) { "source_contract_mismatch" } elseif ($unsupported) { "unsupported" } else { "not_observed" }
        observed_post_endpoint = $postEndpoint
        task_evidence_kind = if ($naturalLanguagePass -and $naturalLanguageCountsValid -and $portableLogMismatches.Count -eq 0) { "read_only_system_diagnostics_task_or_run" } else { "not_observed" }
        read_only_counts = [ordered]@{
            tasks = $readOnlyTasks
            runs = $readOnlyRuns
            chat_messages = $readOnlyChatMessages
            diagnostic_packages = $readOnlyDiagnosticPackages
        }
        natural_language_counts = [ordered]@{
            tasks = $naturalLanguageTasks
            related_tasks = $naturalLanguageRelatedTasks
            runs = $naturalLanguageRuns
            related_runs = $naturalLanguageRelatedRuns
            chat_messages = $naturalLanguageChatMessages
            diagnostic_packages = $naturalLanguageDiagnosticPackages
        }
        natural_language_completion_evidence = [ordered]@{
            level = $naturalLanguageCompletionLevel
            result_verified = [bool]$naturalLanguageResultVerified
            completed_result_evidence = [bool]$naturalLanguageCompletedResultEvidence
            signoff = $false
        }
        unsupported_or_failed = [bool]($unsupported -or $failed)
        clean_machine_signoff = $false
        completed_task_result_signoff = $false
        release_candidate_signoff = $false
        not_signoff_reason = "packaged first-screen/read-only and natural-language submission evidence only"
    }
}
elseif ($latestPortableStatus.found) {
    $contractFailures.Add("latest portable first-screen status log could not be read or was empty")
    [ordered]@{
        found = $true
        path = $latestPortableStatus.path
        last_write_utc = $latestPortableStatus.last_write_utc
        source_contract_status = "source_contract_mismatch"
        parse_error = $latestPortableStatus.error
        first_screen_read_only_pass = $false
        natural_language_submission_evidence = "not_observed"
        natural_language_completion_evidence = [ordered]@{
            level = "not_collected"
            result_verified = $false
            completed_result_evidence = $false
            signoff = $false
        }
        clean_machine_signoff = $false
        completed_task_result_signoff = $false
        release_candidate_signoff = $false
    }
}
else {
    [ordered]@{
        found = $false
        evidence_root = Get-DisplayPath $portableFirstScreenEvidenceRootPath
        source_contract_status = "not_collected_by_this_packet"
        first_screen_read_only_pass = $false
        natural_language_submission_evidence = "not_observed"
        natural_language_completion_evidence = [ordered]@{
            level = "not_collected"
            result_verified = $false
            completed_result_evidence = $false
            signoff = $false
        }
        clean_machine_signoff = $false
        completed_task_result_signoff = $false
        release_candidate_signoff = $false
    }
}

    return $portableLatestSummary
}

