[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$TaskArtifactLabel = "",
    [string]$ResultArtifactLabel = "",
    [string]$UserVisibleResultReview = "",
    [string]$SourceArtifactCheck = "",
    [string]$NextStepActionabilityCheck = "",
    [string]$Reviewer = "",
    [string]$ReviewedAtUtc = "",
    [string[]]$BlockedReason = @(),
    [string[]]$ObservedArtifact = @()
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

function Test-Configured([string]$Value) {
    return -not [string]::IsNullOrWhiteSpace($Value)
}

function Resolve-OutputPath([string]$PathValue, [string]$DefaultRelativePath) {
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

function Redact-TextValue([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    $text = $Value.Trim()

    try {
        if ([System.IO.Path]::IsPathRooted($text)) {
            return Get-DisplayPath $text
        }
    }
    catch {
    }

    try {
        $uri = [Uri]$text
        if ($uri.IsAbsoluteUri -and $uri.Scheme -in @("http", "https", "ws", "wss")) {
            $port = if ($uri.IsDefaultPort) { "" } else { ":$($uri.Port)" }
            $path = if ([string]::IsNullOrWhiteSpace($uri.AbsolutePath) -or $uri.AbsolutePath -eq "/") { "" } else { "/[redacted-path]" }
            return "$($uri.Scheme)://[redacted-host]$port$path"
        }
    }
    catch {
    }

    $text = [regex]::Replace($text, "(?im)\b(system|developer|internal)\s*[:=-]\s*[^\r\n;]+", '$1: [redacted-internal-prompt]')
    $text = [regex]::Replace($text, "(?i)(authorization:\s*bearer\s+)[^\s,;]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(token|api[_-]?key|client_secret|secret|password|code)=([^&\s,;]+)", '${1}=[redacted]')
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{8,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "[A-Za-z]:\\[^\s,;]+", "[redacted-path]")
    $text = [regex]::Replace($text, "(?<!\w)/(?:Users|home)/[^\s,;]+", "[redacted-path]")
    $text = [regex]::Replace($text, "(?i)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[redacted-email]")
    return (Redact-DisplayLabel $text)
}

function ConvertTo-RedactedList([string[]]$Values) {
    $items = New-Object System.Collections.Generic.List[string]
    foreach ($value in $Values) {
        $redacted = Redact-TextValue $value
        if (Test-Configured $redacted) {
            $items.Add($redacted)
        }
    }
    return @($items)
}

function New-ReviewField([string]$Id, [string]$Label, [string]$Value, [string]$Prompt) {
    $redacted = Redact-TextValue $Value
    $status = if (Test-Configured $redacted) {
        "recorded_unverified_by_this_helper"
    }
    else {
        "missing"
    }

    return [ordered]@{
        id = $Id
        label = $Label
        status = $status
        required = $true
        value_redacted = $redacted
        prompt = $Prompt
        verified_by_this_helper = $false
    }
}

function Get-ReviewFieldById($Fields, [string]$Id) {
    foreach ($field in $Fields) {
        if ([string]$field["id"] -eq $Id) {
            return $field
        }
    }
    return $null
}

function Test-NoBlockingReason([string]$Value) {
    if (-not (Test-Configured $Value)) {
        return $false
    }

    $normalized = $Value.Trim().ToLowerInvariant()
    return $normalized -in @("none", "no blocker", "not blocked", "no blocked reason", "not applicable", "n/a")
}

$evidenceRootPath = Resolve-OutputPath $EvidenceRoot ".tmp\result-quality-review"
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $evidenceRootPath "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$jsonPath = Join-Path $runRoot "result-quality-review.redacted.json"
$markdownPath = Join-Path $runRoot "result-quality-review.redacted.md"

$reviewFields = @(
    New-ReviewField "task_result_artifact.task_artifact_label" "Task artifact label" $TaskArtifactLabel "Record the candidate task id, run id, status-log label, or other redacted label for the task being reviewed."
    New-ReviewField "task_result_artifact.result_artifact_label" "Result artifact label" $ResultArtifactLabel "Record the redacted user-visible result artifact label, such as exported result, task detail, screenshot label, or status-log result label."
    New-ReviewField "manual_checks.user_visible_result_review" "User-visible result review" $UserVisibleResultReview "Confirm the visible answer/result matches what a beginner user would expect and note any gap."
    New-ReviewField "manual_checks.source_artifact_check" "Source/artifact check" $SourceArtifactCheck "Confirm cited sources, generated files, screenshots, logs, or other artifacts exist and match the visible result."
    New-ReviewField "manual_checks.next_step_actionability_check" "Next-step/actionability check" $NextStepActionabilityCheck "Confirm the result tells the user what happened, what remains blocked, and the next action in plain language."
    New-ReviewField "reviewer.identity" "Reviewer" $Reviewer "Record the reviewer name, role, handle, or team label after redaction."
    New-ReviewField "reviewer.reviewed_at_utc" "Reviewed at UTC" $ReviewedAtUtc "Record the review timestamp in UTC, for example 2026-06-09T12:34:56Z."
)

$blockedReasons = ConvertTo-RedactedList $BlockedReason
$observedArtifacts = ConvertTo-RedactedList $ObservedArtifact
$missingFields = New-Object System.Collections.Generic.List[string]
foreach ($field in $reviewFields) {
    if ($field["status"] -eq "missing") {
        $missingFields.Add([string]$field["id"])
    }
}
if ($blockedReasons.Count -eq 0) {
    $missingFields.Add("reviewer.blocked_reason_or_none")
}

$taskArtifactLabelField = Get-ReviewFieldById $reviewFields "task_result_artifact.task_artifact_label"
$resultArtifactLabelField = Get-ReviewFieldById $reviewFields "task_result_artifact.result_artifact_label"
$userVisibleResultReviewField = Get-ReviewFieldById $reviewFields "manual_checks.user_visible_result_review"
$sourceArtifactCheckField = Get-ReviewFieldById $reviewFields "manual_checks.source_artifact_check"
$nextStepActionabilityField = Get-ReviewFieldById $reviewFields "manual_checks.next_step_actionability_check"
$reviewerIdentityField = Get-ReviewFieldById $reviewFields "reviewer.identity"
$reviewedAtField = Get-ReviewFieldById $reviewFields "reviewer.reviewed_at_utc"
$issues = New-Object System.Collections.Generic.List[string]
$reviewedAtRedacted = [string]$reviewedAtField["value_redacted"]
$timestampStatus = "missing"
if (Test-Configured $reviewedAtRedacted) {
    try {
        $parsedTimestamp = [DateTimeOffset]::Parse($reviewedAtRedacted)
        $timestampStatus = if ($parsedTimestamp.Offset -eq [TimeSpan]::Zero) { "recorded_utc" } else { "recorded_non_utc_offset" }
        if ($timestampStatus -ne "recorded_utc") {
            $issues.Add("reviewed_at_utc is not UTC")
        }
    }
    catch {
        $timestampStatus = "invalid_timestamp"
        $issues.Add("reviewed_at_utc could not be parsed")
    }
}

$hasBlockingReason = $false
foreach ($reason in $blockedReasons) {
    if (-not (Test-NoBlockingReason $reason)) {
        $hasBlockingReason = $true
    }
}

$summaryStatus = if ($missingFields.Count -gt 0) {
    "blocked_missing_fields"
}
elseif ($issues.Count -gt 0) {
    "blocked_invalid_fields"
}
elseif ($hasBlockingReason) {
    "blocked_reason_recorded"
}
else {
    "manual_review_fields_recorded_not_signoff"
}
$reviewFieldsComplete = [bool]($missingFields.Count -eq 0 -and $issues.Count -eq 0 -and -not $hasBlockingReason)

$packet = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    generated_by = "scripts/collect_result_quality_review_packet.ps1"
    marker = "NOT_RESULT_QUALITY_SIGNOFF"
    outputs = [ordered]@{
        redacted_json = Get-DisplayPath $jsonPath
        redacted_markdown = Get-DisplayPath $markdownPath
    }
    readonly_scope = [ordered]@{
        starts_product_processes = $false
        performs_network_requests = $false
        uploads_external_services = $false
        installs_dependencies = $false
        reads_only_operator_supplied_labels = $true
        writes_only_result_quality_review_packet_artifacts = $true
    }
    redaction = [ordered]@{
        output_policy = "redacted JSON and Markdown only"
        path_policy = "workspace-relative paths or file labels only"
        raw_logs_included = $false
        screenshots_or_artifacts_copied = $false
        secrets_or_tokens_read_intentionally = $false
    }
    summary = [ordered]@{
        status = $summaryStatus
        blocked = ($summaryStatus -ne "manual_review_fields_recorded_not_signoff")
        review_fields_complete = $reviewFieldsComplete
        result_quality_claim_blocked = $true
        separate_human_signoff_required = $true
        missing_field_count = $missingFields.Count
        issue_count = $issues.Count
        result_quality_signoff = $false
        signoff = $false
        claim_allowed = $false
        completed_result_evidence = $false
        release_candidate_signoff = $false
        release_signoff = $false
        template_is_signoff = $false
    }
    claim_controls = [ordered]@{
        claim_allowed = $false
        result_quality_claim_blocked = $true
        separate_human_signoff_required = $true
        result_quality_signoff = $false
        completed_result_evidence = $false
        not_completed_result_evidence = $true
        rc_signoff = $false
        release_signoff = $false
        packet_is_rc_signoff = $false
        packet_is_release_signoff = $false
    }
    task_result_artifact = [ordered]@{
        task_artifact_label = [string]$taskArtifactLabelField["value_redacted"]
        result_artifact_label = [string]$resultArtifactLabelField["value_redacted"]
        observed_artifacts_redacted = @($observedArtifacts)
    }
    manual_checks = [ordered]@{
        user_visible_result_review = $userVisibleResultReviewField
        source_artifact_check = $sourceArtifactCheckField
        next_step_actionability_check = $nextStepActionabilityField
    }
    reviewer = [ordered]@{
        identity_redacted = [string]$reviewerIdentityField["value_redacted"]
        reviewed_at_utc = $reviewedAtRedacted
        timestamp_status = $timestampStatus
        blocked_reason_redacted = @($blockedReasons)
    }
    missing_required_fields = @($missingFields)
    issues_redacted = @($issues)
    review_template = [ordered]@{
        template_status = "manual_result_quality_review_required"
        human_decision = "pending"
        checklist = @($reviewFields)
        must_not_be_recorded_as = @(
            "completed-result evidence",
            "natural-language result-quality sign-off",
            "Task Workspace sign-off",
            "release-candidate sign-off",
            "release sign-off"
        )
    }
    next_manual_evidence_needed = @(
        "Have a human inspect the actual user-visible result and source/artifact labels outside this helper.",
        "Rerun this helper with every required review field plus -BlockedReason none, or with a redacted blocked reason.",
        "Keep this packet separate from completion_evidence; it is not completed-result evidence.",
        "Do not use this packet to approve an RC, release, or public claim."
    )
}

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# Natural-Language Result Quality Review Packet")
$markdownLines.Add("")
$markdownLines.Add("- Marker: $($packet.marker)")
$markdownLines.Add("- Generated: $($packet.generated_at_utc)")
$markdownLines.Add("- JSON: $($packet.outputs.redacted_json)")
$markdownLines.Add("- Markdown: $($packet.outputs.redacted_markdown)")
$markdownLines.Add("- Status: $($packet.summary.status)")
$markdownLines.Add("- Review fields complete: $($packet.summary.review_fields_complete)")
$markdownLines.Add("- Result-quality claim blocked: true")
$markdownLines.Add("- Separate human sign-off required: true")
$markdownLines.Add("- Signoff: false")
$markdownLines.Add("- Claim allowed: false")
$markdownLines.Add("- Scope: read-only helper; no product process starts, no network requests, no uploads, no dependency install.")
$markdownLines.Add("")
$markdownLines.Add("## Red Line")
$markdownLines.Add("- NOT_RESULT_QUALITY_SIGNOFF")
$markdownLines.Add("- This packet is not completed-result evidence, not result-quality sign-off, not RC sign-off, and not release sign-off.")
$markdownLines.Add("- result_quality_signoff=false and claim_allowed=false remain fixed even when all manual fields are recorded.")
$markdownLines.Add("- review_fields_complete only means the helper received every required checklist field; result_quality_claim_blocked=true and separate_human_signoff_required=true remain fixed.")
$markdownLines.Add("")
$markdownLines.Add("## Missing Or Blocked")
if ($packet.missing_required_fields.Count -eq 0 -and $packet.issues_redacted.Count -eq 0 -and -not $hasBlockingReason) {
    $markdownLines.Add("- none for the template fields; a separate human sign-off artifact is still required before any claim")
}
else {
    foreach ($field in $packet.missing_required_fields) {
        $markdownLines.Add("- missing: $field")
    }
    foreach ($issue in $packet.issues_redacted) {
        $markdownLines.Add("- blocked: $issue")
    }
    foreach ($reason in $packet.reviewer.blocked_reason_redacted) {
        if (-not (Test-NoBlockingReason $reason)) {
            $markdownLines.Add("- blocked_reason: $reason")
        }
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Candidate")
$markdownLines.Add("- Task artifact label: $($packet.task_result_artifact.task_artifact_label)")
$markdownLines.Add("- Result artifact label: $($packet.task_result_artifact.result_artifact_label)")
$markdownLines.Add("- Reviewer: $($packet.reviewer.identity_redacted)")
$markdownLines.Add("- Reviewed at UTC: $($packet.reviewer.reviewed_at_utc)")
$markdownLines.Add("- Timestamp status: $($packet.reviewer.timestamp_status)")
$markdownLines.Add("")
$markdownLines.Add("## Manual Checks")
foreach ($field in @(
    $packet.manual_checks.user_visible_result_review,
    $packet.manual_checks.source_artifact_check,
    $packet.manual_checks.next_step_actionability_check
)) {
    $markdownLines.Add("- $($field.id): status=$($field.status); value=$($field.value_redacted)")
}
$markdownLines.Add("")
$markdownLines.Add("## Observed Artifact Labels")
if ($packet.task_result_artifact.observed_artifacts_redacted.Count -eq 0) {
    $markdownLines.Add("- none recorded")
}
else {
    foreach ($artifact in $packet.task_result_artifact.observed_artifacts_redacted) {
        $markdownLines.Add("- $artifact")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Must Not Be Recorded As")
foreach ($item in $packet.review_template.must_not_be_recorded_as) {
    $markdownLines.Add("- $item")
}
$markdown = $markdownLines -join "`n"

$packet | ConvertTo-Json -Depth 14 | Set-Content -LiteralPath $jsonPath -Encoding utf8
$markdown | Set-Content -LiteralPath $markdownPath -Encoding utf8

Write-Host "Natural-language result quality review packet"
Write-Host "Marker: $($packet.marker)"
Write-Host "Redacted JSON: $($packet.outputs.redacted_json)"
Write-Host "Redacted Markdown: $($packet.outputs.redacted_markdown)"
Write-Host "Status: $($packet.summary.status)"
Write-Host "Signoff: false"
Write-Host "Claim allowed: false"
Write-Host ""
Write-Host $markdown

if ($packet.summary.status -eq "manual_review_fields_recorded_not_signoff") {
    exit 0
}

exit 1
