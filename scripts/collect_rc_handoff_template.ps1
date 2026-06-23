[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$CandidateCommit = "",
    [string]$BuildId = "",
    [string]$Platform = "",
    [string[]]$ArtifactLabel = @(),
    [string[]]$GateCommand = @(),
    [string[]]$GateExit = @(),
    [string]$StrictStateSource = "",
    [string[]]$ManualP1Check = @(),
    [string[]]$Waiver = @(),
    [string[]]$ResidualRisk = @()
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

function Redact-DisplayLabel([string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Label)) {
        return ""
    }

    $text = $Label.Trim()
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{4,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "(?i)\b(?:contoso|acme|customer)[A-Za-z0-9._-]*", "[redacted-org]")
    $text = [regex]::Replace($text, "(?i)([?&](?:token|api[_-]?key|client_secret|secret|password|code)=)[^&\s]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)([?&](?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=)[^&\s]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)=[A-Za-z0-9._~+/=-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=[A-Za-z0-9._~+/=-]+", '${1}[redacted-sensitive]=[redacted]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:token|api[_-]?key|secret|password|code)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
    $text = [regex]::Replace($text, "(?i)(^|[._\-\s])(?:session|cookie|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)(?!\=)(?:[._\-][A-Za-z0-9._-]+)?", '${1}[redacted-sensitive]')
    $text = [regex]::Replace($text, "(?i)\bhttps?://[^/\s\\]+", "https://[redacted-host]")
    $text = [regex]::Replace($text, "(?i)\bwss?://[^/\s\\]+", "wss://[redacted-host]")
    $text = [regex]::Replace($text, "\b(?:\d{1,3}\.){3}\d{1,3}\b", "[redacted-host]")
    return $text
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

    $rootPrefix = $resolvedRoot.TrimEnd([char[]]@("\", "/")) + [System.IO.Path]::DirectorySeparatorChar
    if ($fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $relativePath = $fullPath.Substring($rootPrefix.Length).Replace("/", "\")
        return (Redact-DisplayLabel $relativePath)
    }

    return (Redact-DisplayLabel (Split-Path -Leaf $fullPath))
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

    $text = [regex]::Replace($text, "(?i)(authorization:\s*bearer\s+)[^\s,;]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)\b(set-cookie|cookie)\s*[:=]\s*[^,\r\n]+", '${1}: [redacted]')
    $text = [regex]::Replace($text, "(?i)\b(session|cookie|token|api[_-]?key|client_secret|secret|password|code|pairing[_-]?code|pairingCode|one[_-]?time[_-]?code|oneTimeCode|otp)=([^&\s,;]+)", '${1}=[redacted]')
    $text = [regex]::Replace($text, "(?i)\b(pairing\s+code|one[-\s]?time\s+(?:code|passcode|password)|otp)\s*[:=]?\s+[A-Za-z0-9._-]{4,}", '${1} [redacted]')
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{8,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "[A-Za-z]:\\[^\s,;]+", "[redacted-path]")
    $text = [regex]::Replace($text, "(?<!\w)/(?:Users|home)/[^\s,;]+", "[redacted-path]")
    return (Redact-DisplayLabel $text)
}

function Test-ActionableHandoffValue([string]$Value, [string]$Kind = "general") {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $false
    }

    $text = $Value.Trim()
    $lower = $text.ToLowerInvariant()
    if ($text -match "<[^>]+>") {
        return $false
    }
    if ($lower -in @("todo", "to do", "tbd", "pending", "unknown", "fixme", "placeholder")) {
        return $false
    }
    if ($lower -match "^(?:todo|to do|tbd|pending|unknown|fixme|placeholder)(?:$|[\s:._-])") {
        return $false
    }
    if ($Kind -eq "gate_exit" -and $lower -match "\b(?:todo|tbd|pending|unknown|fixme|placeholder)\b") {
        return $false
    }
    if ($Kind -ne "waiver" -and $lower -in @("none", "n/a", "na", "not applicable")) {
        return $false
    }
    if ($Kind -eq "waiver" -and ($lower -in @("waiver", "waiver requested", "requested", "needs waiver") -or $lower -match "^(?:waiver\s+)?requested(?:$|[\s:._-])|^needs\s+waiver(?:$|[\s:._-])|^waiver\s+pending(?:$|[\s:._-])")) {
        return $false
    }
    if ($Kind -eq "gate_exit" -and $lower -notmatch "(?i)(exit|code|status|pass|fail|success|error|blocked|\b0\b|\b1\b)") {
        return $false
    }
    return $true
}

function ConvertTo-RedactedList([string[]]$Values, [string]$Kind = "general") {
    $items = New-Object System.Collections.Generic.List[string]
    foreach ($value in @($Values)) {
        $redacted = Redact-TextValue $value
        if ((Test-Configured $redacted) -and (Test-ActionableHandoffValue $value $Kind) -and (Test-ActionableHandoffValue $redacted $Kind)) {
            $items.Add($redacted)
        }
    }
    return @($items)
}

function Expand-DelimitedValues([string[]]$Values) {
    $items = New-Object System.Collections.Generic.List[string]
    foreach ($value in @($Values)) {
        if ([string]::IsNullOrWhiteSpace($value)) {
            continue
        }

        $parts = [regex]::Split($value, "\r?\n|;;")
        foreach ($part in @($parts)) {
            $trimmed = [string]$part
            if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
                $items.Add($trimmed.Trim())
            }
        }
    }
    return @($items)
}

function Test-ArrayContainsText($Values, [string]$Needle) {
    foreach ($item in @($Values)) {
        if ([string]$item -eq $Needle) {
            return $true
        }
    }
    return $false
}

function New-MissingFieldHint([string]$FieldName) {
    $howToCollect = "Record this field in the manual RC handoff, then rerun the helper with the matching parameter."
    $helperArgument = "<matching helper parameter>"

    switch ($FieldName) {
        "candidate.commit_or_build_id" {
            $howToCollect = "Record the candidate commit SHA and/or build identifier that reviewers are evaluating."
            $helperArgument = "-CandidateCommit <commit SHA> or -BuildId <build id>"
            break
        }
        "candidate.platform" {
            $howToCollect = "Record the platform for this candidate, such as Windows x64, macOS arm64, or Android."
            $helperArgument = "-Platform <platform>"
            break
        }
        "artifact_labels" {
            $howToCollect = "Record redacted labels for each installer, portable archive, mobile build, or artifact under review."
            $helperArgument = "-ArtifactLabel <redacted artifact label>"
            break
        }
        "gate_results.commands_and_exits" {
            $howToCollect = "Record the exact gate commands and their full exit status from the RC run."
            $helperArgument = "-GateCommand <exact command> -GateExit <exit code/status>"
            break
        }
        "gate_results.commands_and_exits_count_match" {
            $howToCollect = "Provide one exit status for every exact gate command, in the same order."
            $helperArgument = '-GateCommand "npm run qa:gate;;npm run release:check" -GateExit "exit 0;;exit 0"'
            break
        }
        "strict_state_source" {
            $howToCollect = "Record the strict state-machine or release gate source used to judge the candidate."
            $helperArgument = "-StrictStateSource <state source, version, or file label>"
            break
        }
        "manual_p1_checks" {
            $howToCollect = "Record each manual P1 check, its observed status, and the reviewed artifact label."
            $helperArgument = "-ManualP1Check <check id/status/artifact label>"
            break
        }
        "waivers" {
            $howToCollect = "Record either 'none' or every waiver with owner, reason, expiry, and follow-up."
            $helperArgument = "-Waiver <none or owner/reason/expiry/follow-up>"
            break
        }
        "residual_risks" {
            $howToCollect = "Record the remaining risk, owner, user impact, and follow-up."
            $helperArgument = "-ResidualRisk <risk/owner/follow-up>"
            break
        }
    }

    return [ordered]@{
        field = $FieldName
        how_to_collect = $howToCollect
        helper_argument = $helperArgument
    }
}

function New-RecordedEntry([string]$Kind, [string]$Value) {
    return [ordered]@{
        kind = $Kind
        value = $Value
        status = "recorded_unverified_by_this_helper"
    }
}

$evidenceRootPath = Resolve-OutputPath $EvidenceRoot ".tmp\rc-handoff-template"
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $evidenceRootPath "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$jsonPath = Join-Path $runRoot "rc-handoff-template.redacted.json"
$markdownPath = Join-Path $runRoot "rc-handoff-template.redacted.md"

$redactedCandidateCommit = Redact-TextValue $CandidateCommit
$redactedBuildId = Redact-TextValue $BuildId
$redactedPlatform = Redact-TextValue $Platform
$expandedArtifactLabels = @(Expand-DelimitedValues $ArtifactLabel)
$expandedGateCommands = @(Expand-DelimitedValues $GateCommand)
$expandedGateExits = @(Expand-DelimitedValues $GateExit)
$expandedManualP1Checks = @(Expand-DelimitedValues $ManualP1Check)
$expandedWaivers = @(Expand-DelimitedValues $Waiver)
$expandedResidualRisks = @(Expand-DelimitedValues $ResidualRisk)

$redactedArtifacts = @(ConvertTo-RedactedList $expandedArtifactLabels "artifact")
$redactedGateCommands = @(ConvertTo-RedactedList $expandedGateCommands "gate_command")
$redactedGateExits = @(ConvertTo-RedactedList $expandedGateExits "gate_exit")
$redactedStrictStateSource = Redact-TextValue $StrictStateSource
$redactedManualP1Checks = @(ConvertTo-RedactedList $expandedManualP1Checks "manual_p1")
$redactedWaivers = @(ConvertTo-RedactedList $expandedWaivers "waiver")
$redactedResidualRisks = @(ConvertTo-RedactedList $expandedResidualRisks "residual_risk")

$missingFields = New-Object System.Collections.Generic.List[string]
$candidateCommitActionable = (Test-ActionableHandoffValue $CandidateCommit "candidate") -and (Test-ActionableHandoffValue $redactedCandidateCommit "candidate")
$buildIdActionable = (Test-ActionableHandoffValue $BuildId "candidate") -and (Test-ActionableHandoffValue $redactedBuildId "candidate")
$platformActionable = (Test-ActionableHandoffValue $Platform "platform") -and (Test-ActionableHandoffValue $redactedPlatform "platform")
$strictStateSourceActionable = (Test-ActionableHandoffValue $StrictStateSource "strict_state_source") -and (Test-ActionableHandoffValue $redactedStrictStateSource "strict_state_source")

if (-not ($candidateCommitActionable -or $buildIdActionable)) {
    $missingFields.Add("candidate.commit_or_build_id")
}
if (-not $platformActionable) {
    $missingFields.Add("candidate.platform")
}
if ($redactedArtifacts.Count -eq 0) {
    $missingFields.Add("artifact_labels")
}
if ($redactedGateCommands.Count -eq 0 -or $redactedGateExits.Count -eq 0) {
    $missingFields.Add("gate_results.commands_and_exits")
}
elseif ($redactedGateCommands.Count -ne $redactedGateExits.Count) {
    $missingFields.Add("gate_results.commands_and_exits_count_match")
}
if (-not $strictStateSourceActionable) {
    $missingFields.Add("strict_state_source")
}
if ($redactedManualP1Checks.Count -eq 0) {
    $missingFields.Add("manual_p1_checks")
}
if ($redactedWaivers.Count -eq 0) {
    $missingFields.Add("waivers")
}
if ($redactedResidualRisks.Count -eq 0) {
    $missingFields.Add("residual_risks")
}

$missingFieldHints = @()
foreach ($field in $missingFields) {
    $missingFieldHints += ,(New-MissingFieldHint ([string]$field))
}
$missingFieldNames = @()
foreach ($field in $missingFields) {
    $missingFieldNames += [string]$field
}

$gateEntries = New-Object System.Collections.Generic.List[object]
$gateEntryCount = [Math]::Max($redactedGateCommands.Count, $redactedGateExits.Count)
for ($i = 0; $i -lt $gateEntryCount; $i++) {
    $command = if ($i -lt $redactedGateCommands.Count) { [string]$redactedGateCommands[$i] } else { "uncollected" }
    $exitStatus = if ($i -lt $redactedGateExits.Count) { [string]$redactedGateExits[$i] } else { "uncollected" }
    $gateEntries.Add([ordered]@{
        command = $command
        exit_status = $exitStatus
        exact_command_and_exit_recorded = (($command -ne "uncollected") -and ($exitStatus -ne "uncollected"))
        status = "recorded_unverified_by_this_helper"
        pass_verified_by_this_helper = $false
    })
}

$artifactEntries = New-Object System.Collections.Generic.List[object]
foreach ($artifact in $redactedArtifacts) {
    $artifactEntries.Add((New-RecordedEntry -Kind "artifact_label" -Value ([string]$artifact)))
}

$manualP1Entries = New-Object System.Collections.Generic.List[object]
foreach ($check in $redactedManualP1Checks) {
    $manualP1Entries.Add((New-RecordedEntry -Kind "manual_p1_check" -Value ([string]$check)))
}

$waiverEntries = New-Object System.Collections.Generic.List[object]
foreach ($waiverItem in $redactedWaivers) {
    $waiverEntries.Add((New-RecordedEntry -Kind "waiver" -Value ([string]$waiverItem)))
}

$residualRiskEntries = New-Object System.Collections.Generic.List[object]
foreach ($risk in $redactedResidualRisks) {
    $residualRiskEntries.Add((New-RecordedEntry -Kind "residual_risk" -Value ([string]$risk)))
}

$templateStatus = if ($missingFields.Count -gt 0) {
    "manual_rc_handoff_required"
}
else {
    "manual_rc_handoff_recorded_unverified_by_this_helper"
}

$candidateStatus = if ($candidateCommitActionable -or $buildIdActionable) {
    "recorded_unverified_by_this_helper"
}
else {
    "missing_required_field"
}
$platformStatus = if ($platformActionable) {
    "recorded_unverified_by_this_helper"
}
else {
    "missing_required_field"
}
$artifactStatus = if ($artifactEntries.Count -gt 0) {
    "recorded_unverified_by_this_helper"
}
else {
    "missing_required_field"
}
$gateResultsStatus = if ((Test-ArrayContainsText $missingFieldNames "gate_results.commands_and_exits") -or (Test-ArrayContainsText $missingFieldNames "gate_results.commands_and_exits_count_match")) {
    "manual_rc_handoff_required"
}
else {
    "recorded_unverified_by_this_helper"
}
$strictStateSourceStatus = if ($strictStateSourceActionable) {
    "recorded_unverified_by_this_helper"
}
else {
    "missing_required_field"
}
$manualP1Status = if ($manualP1Entries.Count -gt 0) {
    "recorded_unverified_by_this_helper"
}
else {
    "missing_required_field"
}
$waiverStatus = if ($waiverEntries.Count -gt 0) {
    "recorded_unverified_by_this_helper"
}
else {
    "missing_required_field"
}
$residualRiskStatus = if ($residualRiskEntries.Count -gt 0) {
    "recorded_unverified_by_this_helper"
}
else {
    "missing_required_field"
}

$redactedJsonOutputPath = Get-DisplayPath $jsonPath
$redactedMarkdownOutputPath = Get-DisplayPath $markdownPath

$nextHelperCommandTemplate = ".\scripts\collect_rc_handoff_template.ps1 -CandidateCommit <commit SHA> -BuildId <build id> -Platform <platform> -ArtifactLabel <redacted artifact label> -GateCommand <exact gate command> -GateExit <exit code/status> -StrictStateSource <state source label> -ManualP1Check <check/status/artifact label> -Waiver <none or owner/reason/expiry/follow-up> -ResidualRisk <risk/owner/follow-up>"

$artifactEntryItems = @()
foreach ($entry in $artifactEntries) { $artifactEntryItems += $entry }
$gateEntryItems = @()
foreach ($entry in $gateEntries) { $gateEntryItems += $entry }
$manualP1EntryItems = @()
foreach ($entry in $manualP1Entries) { $manualP1EntryItems += $entry }
$waiverEntryItems = @()
foreach ($entry in $waiverEntries) { $waiverEntryItems += $entry }
$residualRiskEntryItems = @()
foreach ($entry in $residualRiskEntries) { $residualRiskEntryItems += $entry }

$packet = [ordered]@{}
$packet["schema_version"] = 1
$packet["generated_at_utc"] = [DateTimeOffset]::UtcNow.ToString("o")
$packet["generated_by"] = "scripts/collect_rc_handoff_template.ps1"
$packet["marker"] = "NOT_RELEASE_CANDIDATE_SIGNOFF"
$packet["outputs"] = [ordered]@{
    redacted_json = $redactedJsonOutputPath
    redacted_markdown = $redactedMarkdownOutputPath
}
$packet["readonly_scope"] = [ordered]@{
    starts_product_processes = $false
    runs_release_commands = $false
    performs_network_requests = $false
    installs_dependencies = $false
    changes_backend_product_logic = $false
    changes_desktop_ui = $false
    changes_mobile_app = $false
    writes_only_rc_handoff_template_artifacts = $true
}
$packet["redaction"] = [ordered]@{
    output_policy = "redacted JSON and Markdown only"
    path_policy = "workspace-relative paths or redacted labels only"
    raw_logs_included = $false
    secrets_or_tokens_read_intentionally = $false
    urls_and_hosts_redacted = $true
}
$packet["summary"] = [ordered]@{
    status = $templateStatus
    release_candidate_signoff = $false
    claim_allowed = $false
    template_is_rc_pass = $false
    template_is_release_signoff = $false
    gate_commands_run_by_this_helper = $false
    missing_required_fields_count = $missingFields.Count
    missing_required_fields = @($missingFieldNames)
}
$packet["signoff_controls"] = [ordered]@{
    release_candidate_signoff = $false
    claim_allowed = $false
    pass_defaults_remain_false = $true
    must_not_tag_publish_or_announce = $true
    reason = "This helper records a redacted handoff template only; a separate human RC decision is required."
}
$packet["candidate"] = [ordered]@{
    commit = $redactedCandidateCommit
    build_id = $redactedBuildId
    platform = $redactedPlatform
    commit_or_build_id_status = $candidateStatus
    platform_status = $platformStatus
}
$packet["artifacts"] = [ordered]@{
    status = $artifactStatus
    labels = @($artifactEntryItems)
}
$packet["gate_results"] = [ordered]@{
    status = $gateResultsStatus
    exact_commands_and_exits_required = $true
    commands_and_exits_count_match = ($redactedGateCommands.Count -eq $redactedGateExits.Count -and $redactedGateCommands.Count -gt 0)
    entries = @($gateEntryItems)
    commands_run_by_this_helper = $false
}
$packet["strict_state_source"] = [ordered]@{
    status = $strictStateSourceStatus
    source = $redactedStrictStateSource
    required = $true
}
$packet["manual_p1_checks"] = [ordered]@{
    status = $manualP1Status
    entries = @($manualP1EntryItems)
}
$packet["waivers"] = [ordered]@{
    status = $waiverStatus
    entries = @($waiverEntryItems)
    required_policy = "Record 'none' or include owner, reason, expiry, and follow-up for every waiver."
}
$packet["residual_risks"] = [ordered]@{
    status = $residualRiskStatus
    entries = @($residualRiskEntryItems)
}
$packet["actionable_handoff"] = [ordered]@{
    status = $templateStatus
    missing_now = @($missingFieldHints)
    next_helper_command_template = $nextHelperCommandTemplate
    beginner_instruction = "Fill every missing field, attach only redacted artifact labels, then hand the JSON/Markdown to the human RC reviewer. Do not use this helper output as an RC pass."
}
$packet["required_fields"] = @(
    "candidate.commit_or_build_id",
    "candidate.platform",
    "artifact_labels",
    "gate_results.commands_and_exits",
    "strict_state_source",
    "manual_p1_checks",
    "waivers",
    "residual_risks"
)
$packet["required_redactions"] = @(
    "user names and organization folders in paths",
    "tokens, API keys, cookies, pairing codes, and one-time codes",
    "private hostnames, LAN IP addresses, and raw URLs",
    "raw logs, screenshots, or recordings unless separately reviewed and labeled",
    "customer, organization, or private artifact names when they are not intended as public labels"
)
$packet["must_not_be_recorded_as"] = @(
    "release-candidate pass",
    "release-candidate sign-off",
    "release sign-off",
    "permission to tag, publish, announce, or ship",
    "waiver approval",
    "manual P1 review approval"
)

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# RC Handoff Template")
$markdownLines.Add("")
$markdownLines.Add("- Marker: $($packet.marker)")
$markdownLines.Add("- Generated: $($packet.generated_at_utc)")
$markdownLines.Add("- JSON: $($packet.outputs.redacted_json)")
$markdownLines.Add("- Markdown: $($packet.outputs.redacted_markdown)")
$markdownLines.Add("- Status: $($packet.summary.status)")
$markdownLines.Add("- release_candidate_signoff=false")
$markdownLines.Add("- claim_allowed=false")
$markdownLines.Add("- Scope: template-only helper; it does not run release commands and does not verify a pass.")
$markdownLines.Add("")
$markdownLines.Add("## Red Line")
$markdownLines.Add("- NOT_RELEASE_CANDIDATE_SIGNOFF")
$markdownLines.Add("- Do not tag, publish, announce, ship, or call the candidate passed from this template.")
$markdownLines.Add("- A separate human RC decision must remain required.")
$markdownLines.Add("")
$markdownLines.Add("## Missing Now")
if ($packet.actionable_handoff.missing_now.Count -eq 0) {
    $markdownLines.Add("- none for the template fields; this is still unverified by the helper and not RC sign-off")
}
else {
    foreach ($hint in $packet.actionable_handoff.missing_now) {
        $markdownLines.Add("- $($hint.field): $($hint.how_to_collect) Rerun with $($hint.helper_argument)")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Current Candidate")
$markdownLines.Add("- Commit: $($packet.candidate.commit)")
$markdownLines.Add("- Build id: $($packet.candidate.build_id)")
$markdownLines.Add("- Platform: $($packet.candidate.platform)")
$markdownLines.Add("- Strict state source: $($packet.strict_state_source.source)")
$markdownLines.Add("")
$markdownLines.Add("## Artifact Labels")
if ($packet.artifacts.labels.Count -eq 0) {
    $markdownLines.Add("- uncollected")
}
else {
    foreach ($artifact in $packet.artifacts.labels) {
        $markdownLines.Add("- $($artifact.value)")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Exact Gate Commands And Exits")
if ($packet.gate_results.entries.Count -eq 0) {
    $markdownLines.Add("- uncollected")
}
else {
    foreach ($entry in $packet.gate_results.entries) {
        $markdownLines.Add("- command: $($entry.command); exit: $($entry.exit_status); verified_by_helper=false")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Manual P1 Checks")
if ($packet.manual_p1_checks.entries.Count -eq 0) {
    $markdownLines.Add("- uncollected")
}
else {
    foreach ($check in $packet.manual_p1_checks.entries) {
        $markdownLines.Add("- $($check.value)")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Waivers")
if ($packet.waivers.entries.Count -eq 0) {
    $markdownLines.Add("- uncollected; record none or owner/reason/expiry/follow-up")
}
else {
    foreach ($waiverItem in $packet.waivers.entries) {
        $markdownLines.Add("- $($waiverItem.value)")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Residual Risks")
if ($packet.residual_risks.entries.Count -eq 0) {
    $markdownLines.Add("- uncollected")
}
else {
    foreach ($risk in $packet.residual_risks.entries) {
        $markdownLines.Add("- $($risk.value)")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Next Helper Command Template")
$markdownLines.Add('```powershell')
$markdownLines.Add($packet.actionable_handoff.next_helper_command_template)
$markdownLines.Add('```')
$markdownLines.Add("")
$markdownLines.Add("## Must Not Be Recorded As")
foreach ($item in $packet.must_not_be_recorded_as) {
    $markdownLines.Add("- $item")
}
$markdown = $markdownLines -join "`n"

$json = $packet | ConvertTo-Json -Depth 16
[System.IO.File]::WriteAllText($jsonPath, $json, $utf8NoBom)
[System.IO.File]::WriteAllText($markdownPath, $markdown, $utf8NoBom)

Write-Host "RC handoff template"
Write-Host "Marker: $($packet.marker)"
Write-Host "Redacted JSON: $($packet.outputs.redacted_json)"
Write-Host "Redacted Markdown: $($packet.outputs.redacted_markdown)"
Write-Host "Status: $($packet.summary.status)"
Write-Host "release_candidate_signoff=false"
Write-Host "claim_allowed=false"
Write-Host ""
Write-Host "What is missing now:"
if ($packet.actionable_handoff.missing_now.Count -eq 0) {
    Write-Host "- no required template fields are missing; this is still not RC sign-off."
}
else {
    foreach ($hint in $packet.actionable_handoff.missing_now) {
        Write-Host "- $($hint.field): rerun with $($hint.helper_argument)"
    }
}
Write-Host ""
Write-Host "Next helper command template:"
Write-Host $packet.actionable_handoff.next_helper_command_template
Write-Host ""
Write-Host "This remains NOT a release-candidate pass; all pass/signoff fields stay false."
Write-Host ""
Write-Host $markdown

exit 0
