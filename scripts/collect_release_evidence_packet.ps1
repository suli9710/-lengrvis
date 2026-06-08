[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$MobilePreflightEvidenceRoot = "",
    [string]$QaEvidenceRoot = "",
    [string]$DiagnosticsReviewEvidenceRoot = "",
    [string]$LocalModelCleanMachineEvidenceRoot = "",
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

function Resolve-InputPath([string]$PathValue, [string]$DefaultRelativePath) {
    $value = if ([string]::IsNullOrWhiteSpace($PathValue)) {
        Join-Path $resolvedRoot $DefaultRelativePath
    }
    elseif ([System.IO.Path]::IsPathRooted($PathValue)) {
        $PathValue
    }
    else {
        Join-Path $resolvedRoot $PathValue
    }

    return [System.IO.Path]::GetFullPath($value)
}

function Get-DisplayPath([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return ""
    }

    try {
        $fullPath = [System.IO.Path]::GetFullPath($PathValue)
    }
    catch {
        return (Redact-DisplayLabel (Split-Path -Leaf $PathValue))
    }

    $rootPrefix = $resolvedRoot.TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
    if ($fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return (Redact-DisplayLabel ($fullPath.Substring($rootPrefix.Length)))
    }

    return (Redact-DisplayLabel (Split-Path -Leaf $fullPath))
}

function Redact-DisplayLabel([string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Label)) {
        return ""
    }

    $text = $Label.Trim()
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{4,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "(?i)\b(?:contoso|acme|customer)[A-Za-z0-9._-]*", "[redacted-org]")
    $text = [regex]::Replace($text, "(?i)([?&](?:token|api[_-]?key|client_secret|secret|password|code)=)[^&\s]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)\bhttps?://[^/\s\\]+", "https://[redacted-host]")
    $text = [regex]::Replace($text, "(?i)\bwss?://[^/\s\\]+", "wss://[redacted-host]")
    $text = [regex]::Replace($text, "\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-host]")
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)=[A-Za-z0-9._-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
    return $text
}

function Read-WorkspaceText([string]$RelativePath) {
    $path = Join-Path $resolvedRoot $RelativePath
    if (-not (Test-Path -LiteralPath $path)) {
        return [pscustomobject]@{
            relative_path = $RelativePath
            exists = $false
            text = ""
        }
    }

    return [pscustomobject]@{
        relative_path = $RelativePath
        exists = $true
        text = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    }
}

function Get-MissingNeedles([string]$Text, [string[]]$Needles) {
    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($needle in $Needles) {
        if ($Text.IndexOf($needle, [System.StringComparison]::Ordinal) -lt 0) {
            $missing.Add($needle)
        }
    }
    return @($missing)
}

function Count-TestContracts([string]$RelativePath) {
    $file = Read-WorkspaceText $RelativePath
    $count = 0
    if ($file.exists) {
        $count = [regex]::Matches(
            $file.text,
            "^(?:async\s+def|def)\s+test_",
            [System.Text.RegularExpressions.RegexOptions]::Multiline
        ).Count
    }

    return [ordered]@{
        path = $RelativePath
        exists = $file.exists
        test_contract_count = $count
    }
}

function Get-ArrayCount($Value) {
    if ($null -eq $Value) {
        return 0
    }
    if ($Value -is [array]) {
        return $Value.Count
    }
    return @($Value).Count
}

function Test-JsonFalse($Value) {
    return ($Value -is [bool]) -and ($Value -eq $false)
}

function Test-JsonTrue($Value) {
    return ($Value -is [bool]) -and ($Value -eq $true)
}

function Test-JsonBool($Value) {
    return ($Value -is [bool])
}

function Get-StrictJsonBoolValue($Value) {
    return (Test-JsonTrue $Value)
}

function Test-JsonIntegerOne($Value) {
    return (($Value -is [int]) -or ($Value -is [long])) -and ([int64]$Value -eq 1)
}

function Test-MobileRedactedHostLabel([string]$Value) {
    return $Value -in @("[redacted-host]", "[loopback]", "[bind-address]")
}

function Test-MobileRedactedHttpOrigin([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    return $Value -match "^https?://\[(?:redacted-host|loopback|bind-address)\](?::\d{1,5})?$"
}

function Test-MobileRedactedWebSocketUrl([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    return $Value -match "^wss?://\[(?:redacted-host|loopback|bind-address)\](?::\d{1,5})?/ws/(?:mobile/approvals|remote/input)$"
}

function Get-SafeMobileHostLabel([string]$Value) {
    if (Test-MobileRedactedHostLabel $Value) {
        return $Value
    }
    return "invalid_redacted"
}

function Get-SafeMobileHttpOrigin([string]$Value) {
    if (Test-MobileRedactedHttpOrigin $Value) {
        return $Value
    }
    return "invalid_redacted"
}

function Get-SafeMobileWebSocketUrl([string]$Value) {
    if (Test-MobileRedactedWebSocketUrl $Value) {
        return $Value
    }
    return "invalid_redacted"
}

function Test-ArrayContainsText($Value, [string]$Needle) {
    foreach ($item in @($Value)) {
        if ([string]$item -eq $Needle) {
            return $true
        }
    }
    return $false
}

function Get-SourceContract([string]$RelativePath, [string[]]$Needles) {
    $file = Read-WorkspaceText $RelativePath
    $missing = if ($file.exists) { Get-MissingNeedles $file.text $Needles } else { @("file_missing") }
    return [ordered]@{
        path = $RelativePath
        exists = $file.exists
        required_markers_present = ($missing.Count -eq 0)
        missing_markers = @($missing)
    }
}

function Find-LatestJsonArtifact([string]$RootPath, [string]$FileName) {
    if (-not (Test-Path -LiteralPath $RootPath)) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            data = $null
            error = ""
        }
    }

    $files = @(
        Get-ChildItem -LiteralPath $RootPath -Recurse -Filter $FileName -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($files.Count -eq 0) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            data = $null
            error = ""
        }
    }

    $latest = $files[0]
    try {
        $data = Get-Content -LiteralPath $latest.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            data = $data
            error = ""
        }
    }
    catch {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            data = $null
            error = "latest JSON artifact could not be parsed"
        }
    }
}

function Find-LatestTextArtifact([string]$RootPath, [string]$FileName) {
    if (-not (Test-Path -LiteralPath $RootPath)) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            text = ""
            error = ""
        }
    }

    $files = @(
        Get-ChildItem -LiteralPath $RootPath -Recurse -Filter $FileName -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($files.Count -eq 0) {
        return [pscustomobject]@{
            found = $false
            path = ""
            last_write_utc = ""
            text = ""
            error = ""
        }
    }

    $latest = $files[0]
    try {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            text = Get-Content -LiteralPath $latest.FullName -Raw -Encoding UTF8
            error = ""
        }
    }
    catch {
        return [pscustomobject]@{
            found = $true
            path = Get-DisplayPath $latest.FullName
            last_write_utc = $latest.LastWriteTimeUtc.ToString("o")
            text = ""
            error = "latest text artifact could not be read"
        }
    }
}

function Get-RegexFirstGroup([string]$Text, [string]$Pattern) {
    $match = [regex]::Match($Text, $Pattern)
    if ($match.Success -and $match.Groups.Count -gt 1) {
        return [string]$match.Groups[1].Value
    }
    return ""
}

function ConvertTo-IntOrZero([string]$Value) {
    $parsed = 0
    if ([int]::TryParse($Value, [ref]$parsed)) {
        return $parsed
    }
    return 0
}

function Get-FirstLogLine([string]$Text, [string]$Pattern) {
    foreach ($line in ($Text -split "\r?\n")) {
        if ($line -match $Pattern) {
            return [string]$line
        }
    }
    return ""
}

$evidenceRootPath = Resolve-InputPath $EvidenceRoot ".tmp\release-evidence-packet"
$mobileEvidenceRootPath = Resolve-InputPath $MobilePreflightEvidenceRoot ".tmp\mobile-lan-wss-preflight"
$qaEvidenceRootPath = Resolve-InputPath $QaEvidenceRoot ".tmp\qa-evidence"
$diagnosticsReviewEvidenceRootPath = Resolve-InputPath $DiagnosticsReviewEvidenceRoot ".tmp\diagnostics-external-review"
$localModelCleanMachineEvidenceRootPath = Resolve-InputPath $LocalModelCleanMachineEvidenceRoot ".tmp\local-model-clean-machine-evidence"
$portableFirstScreenEvidenceRootPath = Resolve-InputPath $PortableFirstScreenEvidenceRoot ".tmp\portable-first-screen-smoke"
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $evidenceRootPath "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$jsonPath = Join-Path $runRoot "release-evidence-packet.redacted.json"
$markdownPath = Join-Path $runRoot "release-evidence-packet.redacted.md"

$contractFailures = New-Object System.Collections.Generic.List[string]

$mobileNeedles = @(
    "evidence-summary.redacted.json",
    "redacted_evidence_summary_path",
    "Token-bearing mobile LAN flows require HTTPS and WSS",
    "This preflight does not use a phone, emulator, camera, QR scanner, or real WSS connection",
    "must not be recorded as real-device pass evidence"
)
$mobileContract = Get-SourceContract "scripts/verify_mobile_lan_wss_preflight.ps1" $mobileNeedles
if (-not $mobileContract.required_markers_present) {
    $contractFailures.Add("mobile LAN/WSS preflight source contract is missing required redaction or non-evidence markers")
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

$latestMobile = Find-LatestJsonArtifact $mobileEvidenceRootPath "evidence-summary.redacted.json"
$mobileLatestSummary = if ($latestMobile.found -and $null -ne $latestMobile.data) {
    $mobileArtifactMismatches = New-Object System.Collections.Generic.List[string]
    $mobileResult = [string]$latestMobile.data.result
    $mobileGeneratedAt = [string]$latestMobile.data.generated_at_utc
    $mobileGeneratedAtParsed = [DateTimeOffset]::MinValue
    $mobileHostRedacted = [string]$latestMobile.data.backend.host_redacted
    $mobilePublicBaseUrlRedacted = [string]$latestMobile.data.backend.public_base_url_redacted
    $mobileApprovalsUrlRedacted = [string]$latestMobile.data.backend.websocket_approvals_url_redacted
    $mobileRemoteInputUrlRedacted = [string]$latestMobile.data.backend.websocket_remote_input_url_redacted
    $mobileTransportSecurityStatus = [string]$latestMobile.data.qr_payload_shape.transport_security_status
    $mobileReadyStatus = $mobileResult -eq "ready_for_manual_real_device_evidence"
    if ($mobileResult -notin @("ready_for_manual_real_device_evidence", "blocked")) {
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
    if (-not (Test-MobileRedactedWebSocketUrl $mobileApprovalsUrlRedacted)) {
        $mobileArtifactMismatches.Add("backend.websocket_approvals_url_redacted is not a safe redacted websocket URL")
    }
    if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileApprovalsUrlRedacted)) {
        $mobileArtifactMismatches.Add("ready mobile preflight is missing backend.websocket_approvals_url_redacted")
    }
    if (-not (Test-MobileRedactedWebSocketUrl $mobileRemoteInputUrlRedacted)) {
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
    if ($mobileArtifactMismatches.Count -gt 0) {
        $contractFailures.Add("latest mobile LAN/WSS preflight artifact failed redacted contract validation")
    }
    [ordered]@{
        found = $true
        path = $latestMobile.path
        last_write_utc = $latestMobile.last_write_utc
        source_contract_status = if ($mobileArtifactMismatches.Count -eq 0) { "valid_redacted_summary" } else { "source_contract_mismatch" }
        mismatch_reasons = @($mobileArtifactMismatches)
        result = if ($mobileArtifactMismatches.Count -eq 0 -and $mobileResult -in @("ready_for_manual_real_device_evidence", "blocked")) { $mobileResult } elseif ($mobileResult -in @("ready_for_manual_real_device_evidence", "blocked")) { "source_contract_mismatch" } else { "invalid_redacted" }
        generated_at_utc = if ($mobileArtifactMismatches -notcontains "generated_at_utc is not a timestamp") { $mobileGeneratedAt } else { "invalid_redacted" }
        backend = [ordered]@{
            host_redacted = Get-SafeMobileHostLabel $mobileHostRedacted
            public_base_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobilePublicBaseUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileHttpOrigin $mobilePublicBaseUrlRedacted }
            websocket_approvals_url_redacted = if ($mobileReadyStatus -and [string]::IsNullOrWhiteSpace($mobileApprovalsUrlRedacted)) { "invalid_redacted" } else { Get-SafeMobileWebSocketUrl $mobileApprovalsUrlRedacted }
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
    'real_install_start_pull_pass = $false',
    "manual_clean_machine_local_model_evidence_required",
    "true local model install pass",
    "true local model pull pass"
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
    if (-not (Test-JsonFalse $latestLocalModelTemplate.data.summary.real_install_start_pull_pass)) {
        $localModelTemplateMismatches.Add("summary.real_install_start_pull_pass is not false")
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
    if ([string]$latestLocalModelTemplate.data.evidence_template.model.status -ne "unverified_by_this_helper") {
        $localModelTemplateMismatches.Add("evidence_template.model.status is not unverified_by_this_helper")
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
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "clean-machine local-model readiness")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing clean-machine local-model readiness")
    }
    if (-not (Test-ArrayContainsText $latestLocalModelTemplate.data.evidence_template.must_not_be_recorded_as "release-candidate sign-off")) {
        $localModelTemplateMismatches.Add("must_not_be_recorded_as is missing release-candidate sign-off")
    }
    if ($localModelTemplateMismatches.Count -gt 0) {
        $contractFailures.Add("latest local-model clean-machine helper artifact failed fail-closed validation")
    }
    $safeLocalModelMarker = if ($localModelTemplateMarker -eq "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS") { $localModelTemplateMarker } else { "invalid_redacted" }
    $safeLocalModelStatus = if ($localModelTemplateStatus -in @("manual_review_ready", "blocked_reason_recorded", "blocked_missing_required_fields")) { $localModelTemplateStatus } else { "invalid_redacted" }
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
        real_install_start_pull_pass = $false
        release_candidate_signoff = $false
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
        real_install_start_pull_pass = $false
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
        real_install_start_pull_pass = $false
        release_candidate_signoff = $false
    }
}

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
    if ($diagnosticsReviewMarker -ne "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF") {
        $diagnosticsReviewMismatches.Add("marker is missing or not NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF")
    }
    if ($reviewStatus -ne "manual_external_review_template_ready") {
        $diagnosticsReviewMismatches.Add("review status is not manual_external_review_template_ready")
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
    if (($null -ne $latestDiagnosticsReview.data.summary.external_sharing_allowed) -and -not (Test-JsonFalse $latestDiagnosticsReview.data.summary.external_sharing_allowed)) {
        $diagnosticsReviewMismatches.Add("summary.external_sharing_allowed is not false")
    }
    if (-not (Test-JsonFalse $latestDiagnosticsReview.data.review_template.public_safe)) {
        $diagnosticsReviewMismatches.Add("review_template.public_safe is not false")
    }
    if ($diagnosticsReviewMismatches.Count -gt 0) {
        $contractFailures.Add("latest diagnostics external-review helper artifact failed fail-closed validation")
    }
    $safeDiagnosticsReviewMarker = if ($diagnosticsReviewMarker -eq "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF") { $diagnosticsReviewMarker } else { "invalid_redacted" }
    $safeReviewStatus = if ($reviewStatus -eq "manual_external_review_template_ready") { $reviewStatus } else { "invalid_redacted" }
    [ordered]@{
        found = $true
        path = $latestDiagnosticsReview.path
        last_write_utc = $latestDiagnosticsReview.last_write_utc
        marker = $safeDiagnosticsReviewMarker
        source_contract_status = if ($diagnosticsReviewMismatches.Count -eq 0) { "valid_not_signoff_template" } else { "source_contract_mismatch" }
        mismatch_reasons = @($diagnosticsReviewMismatches)
        review_status = $safeReviewStatus
        public_safe = $false
        external_sharing_allowed = $false
        human_review_signoff = $false
        template_is_human_signoff = $false
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
    }
}

$settingsNeedles = @(
    "settings local model experience smoke passed; screenshots:",
    "assertCleanMachineSetupPlanContract",
    "clean machine setup plan must not report local model readiness",
    "clean machine verification should not expose local paths",
    "counters.installRequests",
    "settings-local-model-experience-smoke-desktop.png",
    "settings-local-model-experience-smoke-desktop-setup.png",
    "settings-local-model-experience-smoke-narrow.png",
    "settings-local-model-experience-smoke-narrow-setup.png"
)
$settingsContract = Get-SourceContract "desktop/scripts/settings-local-model-experience-smoke.cjs" $settingsNeedles
if (-not $settingsContract.required_markers_present) {
    $contractFailures.Add("Settings local-model smoke source contract is missing required markers")
}

$settingsArtifactNames = @(
    "settings-local-model-experience-smoke-desktop.png",
    "settings-local-model-experience-smoke-desktop-setup.png",
    "settings-local-model-experience-smoke-narrow.png",
    "settings-local-model-experience-smoke-narrow-setup.png"
)
$settingsArtifacts = @(
    foreach ($name in $settingsArtifactNames) {
        $artifactPath = Join-Path $qaEvidenceRootPath $name
        if (Test-Path -LiteralPath $artifactPath) {
            $item = Get-Item -LiteralPath $artifactPath
            [ordered]@{
                name = $name
                path = Get-DisplayPath $artifactPath
                exists = $true
                bytes = [int64]$item.Length
                last_write_utc = $item.LastWriteTimeUtc.ToString("o")
            }
        }
        else {
            [ordered]@{
                name = $name
                path = Get-DisplayPath $artifactPath
                exists = $false
                bytes = 0
                last_write_utc = ""
            }
        }
    }
)
$settingsArtifactsPresent = @($settingsArtifacts | Where-Object { $_.exists }).Count

$packet = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    generated_by = "scripts/collect_release_evidence_packet.ps1"
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
        automated_evidence_items = 7
        indexed_evidence_buckets = 7
        evidence_count_is_not_acceptance_count = $true
        source_contract_failures = $contractFailures.Count
        packet_is_pass = $false
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        real_device_signoff = $false
        release_candidate_signoff = $false
        agent_task_completion_signoff = $false
        result_quality_signoff = $false
        diagnostics_public_safe = $false
        portable_natural_language_scope = "submission_plus_read_only_routing_evidence_only"
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
            expected_marker = "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
            expected_clean_machine_signoff = $false
            expected_install_start_pull_pass = $false
            verify_command = "python -m pytest backend/tests/test_start_app_script.py -q"
            not_signoff = @(
                "not true local model install pass",
                "not true local model start pass",
                "not true local model pull pass",
                "not clean-machine local-model readiness",
                "not release-candidate sign-off"
            )
        }
        diagnostics_external_review = [ordered]@{
            status = if ($diagnosticsContract.required_markers_present) { "manual_review_required_contract_present" } else { "source_contract_missing" }
            source_contract = $diagnosticsContract
            latest_redacted_review_packet = $diagnosticsReviewLatestSummary
            expected_external_review_status = "manual_review_required"
            expected_required_before_external_sharing = $true
            expected_public_safe = $false
            verify_command = "python -m pytest backend/tests/test_system_diagnostics.py -q"
            not_signoff = @(
                "not external public-safety approval",
                "not permission to share diagnostics outside trusted support",
                "not clean-machine diagnostics sign-off",
                "not a human content review sign-off"
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
        "It does not create real phone/emulator camera/QR/WSS/certificate-trust evidence.",
        "It does not make diagnostics packages public-safe; public_safe remains false pending manual external review.",
        "It is not release-candidate sign-off without the release gate command results, candidate id, artifact paths, and manual P1 sign-off."
    )
    next_manual_evidence_needed = @(
        "Clean-machine or packaged-profile local model install/start/pull evidence when local/offline model readiness is claimed.",
        "Real phone/emulator camera or QR pairing path, actual WSS connection, and explicit device certificate trust evidence when mobile LAN/WSS readiness is claimed.",
        "Manual diagnostics package review before any external sharing.",
        "Release-candidate artifact verification and manual P1 sign-off before RC approval."
    )
}

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# Release Evidence Packet Summary")
$markdownLines.Add("")
$markdownLines.Add("- Generated: $($packet.generated_at_utc)")
$markdownLines.Add("- JSON: $($packet.outputs.redacted_json)")
$markdownLines.Add("- Markdown: $($packet.outputs.redacted_markdown)")
$markdownLines.Add("- Status: $($packet.summary.packet_status)")
$markdownLines.Add("- Packet role: evidence index only; packet_is_pass=false; evidence bucket count is not an acceptance count.")
$markdownLines.Add("- Scope: no product process starts, no network requests, no backend/desktop/mobile product changes.")
$markdownLines.Add("")
$markdownLines.Add("## Not Sign-Off")
foreach ($item in $packet.not_clean_machine_or_signoff) {
    $markdownLines.Add("- $item")
}
$markdownLines.Add("")
$markdownLines.Add("## Evidence")
$markdownLines.Add("")
$markdownLines.Add("- Mobile LAN/WSS preflight: $($packet.evidence.mobile_lan_wss_preflight.status); latest summary result=$($packet.evidence.mobile_lan_wss_preflight.latest_redacted_summary.result)")
$portableCompletionEvidence = $packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.natural_language_completion_evidence
$markdownLines.Add("- Portable first-screen smoke: found=$($packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.found), read_only_pass=$($packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.first_screen_read_only_pass), natural_language=$($packet.evidence.portable_first_screen_smoke.latest_redacted_status_log.natural_language_submission_evidence), completion_evidence.level=$($portableCompletionEvidence.level), result_verified=$($portableCompletionEvidence.result_verified), completed_result_evidence=$($portableCompletionEvidence.completed_result_evidence).")
$markdownLines.Add("- Ollama/local-model contracts: $($packet.evidence.ollama_local_model_contracts.contract_count) backend contract tests counted; latest execution not run by this packet.")
$markdownLines.Add("- Local model clean-machine template: found=$($packet.evidence.local_model_clean_machine_template.latest_redacted_clean_machine_template.found), template_status=$($packet.evidence.local_model_clean_machine_template.latest_redacted_clean_machine_template.template_status), clean_machine_signoff=$($packet.evidence.local_model_clean_machine_template.latest_redacted_clean_machine_template.clean_machine_signoff).")
$markdownLines.Add("- Diagnostics external review: expected status=$($packet.evidence.diagnostics_external_review.expected_external_review_status), public_safe=$($packet.evidence.diagnostics_external_review.expected_public_safe).")
$markdownLines.Add("- Diagnostics external review packet: found=$($packet.evidence.diagnostics_external_review.latest_redacted_review_packet.found), review_status=$($packet.evidence.diagnostics_external_review.latest_redacted_review_packet.review_status), public_safe=$($packet.evidence.diagnostics_external_review.latest_redacted_review_packet.public_safe).")
$markdownLines.Add("- Settings local-model smoke: $settingsArtifactsPresent/$($settingsArtifactNames.Count) expected screenshot artifacts present.")
$markdownLines.Add("")
$markdownLines.Add("## Next Manual Evidence")
foreach ($item in $packet.next_manual_evidence_needed) {
    $markdownLines.Add("- $item")
}
$markdown = $markdownLines -join "`n"

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
