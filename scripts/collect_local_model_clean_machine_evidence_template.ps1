[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$Candidate = "",
    [string]$EvidenceMode = "unknown",
    [string]$Platform = "",
    [string]$Runtime = "",
    [string]$RuntimeVersion = "",
    [string]$RuntimeSource = "",
    [string]$Model = "",
    [string]$ModelVersion = "",
    [string]$ModelSource = "",
    [string[]]$BlockedReason = @(),
    [string[]]$Artifact = @()
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

    $text = [regex]::Replace($text, "(?i)(authorization:\s*bearer\s+)[^\s,;]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(bearer\s+)[A-Za-z0-9._~-]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(token|api[_-]?key|secret|password|code)=([^&\s,;]+)", '${1}=[redacted]')
    $text = [regex]::Replace($text, "sk-[A-Za-z0-9._-]{8,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "[A-Za-z]:\\[^\s,;]+", "[redacted-path]")
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

$evidenceRootPath = Resolve-OutputPath $EvidenceRoot ".tmp\local-model-clean-machine-evidence"
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $evidenceRootPath "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$jsonPath = Join-Path $runRoot "local-model-clean-machine-evidence.redacted.json"
$markdownPath = Join-Path $runRoot "local-model-clean-machine-evidence.redacted.md"

$missingFields = New-Object System.Collections.Generic.List[string]
if (-not (Test-Configured $Runtime)) { $missingFields.Add("runtime.name") }
if (-not (Test-Configured $RuntimeVersion)) { $missingFields.Add("runtime.version") }
if (-not (Test-Configured $Model)) { $missingFields.Add("model.name") }
if (-not (Test-Configured $ModelVersion)) { $missingFields.Add("model.version") }

$redactedBlockedReasons = ConvertTo-RedactedList $BlockedReason
$redactedArtifacts = ConvertTo-RedactedList $Artifact
$hasManualBlockedReason = $redactedBlockedReasons.Count -gt 0
$allRequiredFieldsPresent = $missingFields.Count -eq 0

if ((-not $allRequiredFieldsPresent) -and (-not $hasManualBlockedReason)) {
    $redactedBlockedReasons = @(
        "missing runtime/model/version evidence; record the exact blocked reason before claiming local/offline model readiness"
    )
}

$templateStatus = if ($allRequiredFieldsPresent) {
    "manual_review_ready"
}
elseif ($hasManualBlockedReason) {
    "blocked_reason_recorded"
}
else {
    "blocked_missing_required_fields"
}

$packet = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    generated_by = "scripts/collect_local_model_clean_machine_evidence_template.ps1"
    marker = "NOT_REAL_LOCAL_MODEL_INSTALL_START_PULL_PASS"
    outputs = [ordered]@{
        redacted_json = Get-DisplayPath $jsonPath
        redacted_markdown = Get-DisplayPath $markdownPath
    }
    readonly_scope = [ordered]@{
        starts_product_processes = $false
        performs_network_requests = $false
        installs_runtime = $false
        starts_runtime = $false
        pulls_models = $false
        runs_model_inference = $false
        changes_backend_product_logic = $false
        changes_desktop_ui = $false
        writes_only_evidence_template_artifacts = $true
    }
    redaction = [ordered]@{
        path_policy = "workspace-relative paths or file labels only"
        raw_logs_included = $false
        secrets_or_tokens_read = $false
        urls_redacted = $true
    }
    summary = [ordered]@{
        template_status = $templateStatus
        clean_machine_signoff = $false
        local_model_install_pass = $false
        local_model_start_pass = $false
        local_model_pull_pass = $false
        real_install_start_pull_pass = $false
        release_candidate_signoff = $false
        missing_required_fields = @($missingFields)
    }
    evidence_template = [ordered]@{
        template_status = "manual_clean_machine_local_model_evidence_required"
        candidate = Redact-TextValue $Candidate
        evidence_mode = Redact-TextValue $EvidenceMode
        platform = Redact-TextValue $Platform
        runtime = [ordered]@{
            name = Redact-TextValue $Runtime
            version = Redact-TextValue $RuntimeVersion
            source = Redact-TextValue $RuntimeSource
            status = "unverified_by_this_helper"
        }
        model = [ordered]@{
            name = Redact-TextValue $Model
            version = Redact-TextValue $ModelVersion
            source = Redact-TextValue $ModelSource
            status = "unverified_by_this_helper"
        }
        blocked_reason_redacted = @($redactedBlockedReasons)
        observed_artifacts_redacted = @($redactedArtifacts)
        required_fields = @(
            "runtime.name",
            "runtime.version",
            "model.name",
            "model.version",
            "blocked_reason_redacted when any runtime/model/version evidence is unavailable"
        )
        required_redactions = @(
            "user names and organization folders in paths",
            "tokens, API keys, cookies, pairing codes, and one-time codes",
            "private model cache paths",
            "raw console logs unless separately reviewed",
            "non-local hostnames or IP addresses"
        )
        must_not_be_recorded_as = @(
            "true local model install pass",
            "true local model start pass",
            "true local model pull pass",
            "clean-machine local-model readiness",
            "release-candidate sign-off"
        )
        next_manual_evidence_needed = @(
            "Run the candidate on a clean machine or clean packaged profile.",
            "Record the runtime name/version and whether the runtime was already present, installed manually, or blocked.",
            "Record the model name/version or exact blocked reason.",
            "Attach redacted screenshots/log labels that prove the observed user-visible state.",
            "Keep this helper output separate from the actual install/start/pull evidence."
        )
    }
}

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# Local Model Clean-Machine Evidence Template")
$markdownLines.Add("")
$markdownLines.Add("- Marker: $($packet.marker)")
$markdownLines.Add("- Generated: $($packet.generated_at_utc)")
$markdownLines.Add("- JSON: $($packet.outputs.redacted_json)")
$markdownLines.Add("- Markdown: $($packet.outputs.redacted_markdown)")
$markdownLines.Add("- Status: $($packet.summary.template_status)")
$markdownLines.Add("- Scope: read-only helper; no product process starts, no network requests, no runtime install, no runtime start, no model pull.")
$markdownLines.Add("")
$markdownLines.Add("## Required Fields")
foreach ($field in $packet.evidence_template.required_fields) {
    $markdownLines.Add("- $field")
}
$markdownLines.Add("")
$markdownLines.Add("## Current Entry")
$markdownLines.Add("- Candidate: $($packet.evidence_template.candidate)")
$markdownLines.Add("- Evidence mode: $($packet.evidence_template.evidence_mode)")
$markdownLines.Add("- Platform: $($packet.evidence_template.platform)")
$markdownLines.Add("- Runtime: $($packet.evidence_template.runtime.name) $($packet.evidence_template.runtime.version)")
$markdownLines.Add("- Model: $($packet.evidence_template.model.name) $($packet.evidence_template.model.version)")
$markdownLines.Add("")
$markdownLines.Add("## Blocked Reason Redacted")
foreach ($reason in $packet.evidence_template.blocked_reason_redacted) {
    $markdownLines.Add("- $reason")
}
$markdownLines.Add("")
$markdownLines.Add("## Observed Artifacts Redacted")
if ($packet.evidence_template.observed_artifacts_redacted.Count -eq 0) {
    $markdownLines.Add("- none recorded")
}
else {
    foreach ($artifact in $packet.evidence_template.observed_artifacts_redacted) {
        $markdownLines.Add("- $artifact")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Not Sign-Off")
foreach ($item in $packet.evidence_template.must_not_be_recorded_as) {
    $markdownLines.Add("- $item")
}
$markdown = $markdownLines -join "`n"

$packet | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $jsonPath -Encoding utf8
$markdown | Set-Content -LiteralPath $markdownPath -Encoding utf8

Write-Host "Local model clean-machine evidence template"
Write-Host "Marker: $($packet.marker)"
Write-Host "Redacted JSON: $($packet.outputs.redacted_json)"
Write-Host "Redacted Markdown: $($packet.outputs.redacted_markdown)"
Write-Host ""
Write-Host $markdown

exit 0
