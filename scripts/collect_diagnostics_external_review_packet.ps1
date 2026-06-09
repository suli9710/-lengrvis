[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$DiagnosticsRoot = "",
    [string]$DiagnosticsPackagePath = "",
    [string]$EvidenceRoot = ""
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

function Resolve-WorkspacePath([string]$PathValue, [string]$DefaultRelativePath) {
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

function Resolve-DefaultDiagnosticsRoot {
    if (Test-Configured $DiagnosticsRoot) {
        return Resolve-WorkspacePath $DiagnosticsRoot ".tmp\diagnostic-packages"
    }

    if (Test-Configured $env:LENGRVIS_DATA_DIR) {
        return [System.IO.Path]::GetFullPath((Join-Path $env:LENGRVIS_DATA_DIR "diagnostic-packages"))
    }

    return Resolve-WorkspacePath "" ".tmp\diagnostic-packages"
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
            $path = if ([string]::IsNullOrWhiteSpace($uri.AbsolutePath)) { "" } else { $uri.AbsolutePath }
            return "$($uri.Scheme)://[redacted-host]$port$path"
        }
    }
    catch {
    }

    $text = [regex]::Replace($text, "(?i)(authorization:\s*bearer\s+)[^\s,;]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", '${1}[redacted]')
    $text = [regex]::Replace($text, "(?i)(token|api[_-]?key|secret|password|code)=([^&\s,;]+)", '${1}=[redacted]')
    $text = [regex]::Replace($text, "sk-(?:proj-)?[A-Za-z0-9._-]{8,}", "sk-[redacted]")
    $text = [regex]::Replace($text, "[A-Za-z]:\\[^\s,;]+", "[redacted-path]")
    $text = [regex]::Replace($text, "/Users/[^/\s,;]+/[^\s,;]+", "[redacted-path]")
    return $text
}

function Get-JsonValue($Object, [string[]]$Path) {
    $current = $Object
    foreach ($name in $Path) {
        if ($null -eq $current) {
            return $null
        }
        $property = $current.PSObject.Properties[$name]
        if ($null -eq $property) {
            return $null
        }
        $current = $property.Value
    }
    return $current
}

function Get-StringJsonValue($Object, [string[]]$Path) {
    $value = Get-JsonValue $Object $Path
    if ($null -eq $value) {
        return ""
    }
    return Redact-TextValue ([string]$value)
}

function Get-FalseObservation($Value) {
    if ($null -eq $Value) {
        return "missing"
    }
    if ($Value -eq $false) {
        return "false"
    }
    return "not_false_ignored"
}

function Get-RequiredObservation($Value) {
    if ($null -eq $Value) {
        return "missing"
    }
    if ($Value -eq $true) {
        return "required"
    }
    return "not_required_in_input_but_required_by_template"
}

function Find-LatestDiagnosticsPackage([string]$RootPath) {
    if (-not (Test-Path -LiteralPath $RootPath)) {
        return $null
    }

    $files = @(
        Get-ChildItem -LiteralPath $RootPath -Recurse -Filter "*.json" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending
    )
    if ($files.Count -eq 0) {
        return $null
    }
    return $files[0]
}

$evidenceRootPath = Resolve-WorkspacePath $EvidenceRoot ".tmp\diagnostics-external-review"
$runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$runRoot = Join-Path $evidenceRootPath "run-$runStamp"
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null
$jsonPath = Join-Path $runRoot "diagnostics-external-review.redacted.json"
$markdownPath = Join-Path $runRoot "diagnostics-external-review.redacted.md"

$diagnosticsRootPath = Resolve-DefaultDiagnosticsRoot
$selectionMode = if (Test-Configured $DiagnosticsPackagePath) { "specified" } else { "latest" }
$selectedPackage = $null
$inputExists = $false
$inputParseStatus = "not_read"
$parseError = ""
$package = $null
$diagnostics = $null
$redaction = $null
$externalReview = $null
$issues = New-Object System.Collections.Generic.List[string]

if ($selectionMode -eq "specified") {
    $specifiedPath = Resolve-WorkspacePath $DiagnosticsPackagePath ""
    if (Test-Path -LiteralPath $specifiedPath) {
        $selectedPackage = Get-Item -LiteralPath $specifiedPath
    }
    else {
        $issues.Add("specified diagnostics package was not found")
    }
}
else {
    $selectedPackage = Find-LatestDiagnosticsPackage $diagnosticsRootPath
    if ($null -eq $selectedPackage) {
        $issues.Add("no diagnostics package JSON was found under the diagnostics root")
    }
}

if ($null -ne $selectedPackage) {
    $inputExists = $true
    try {
        $package = Get-Content -LiteralPath $selectedPackage.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        $inputParseStatus = "parsed"
        $diagnostics = Get-JsonValue $package @("diagnostics")
        if ($null -eq $diagnostics) {
            $diagnostics = $package
        }
        $redaction = Get-JsonValue $diagnostics @("support_package_redaction")
        $externalReview = Get-JsonValue $redaction @("external_review")

        $diagnosticScope = Get-StringJsonValue $diagnostics @("diagnostic_scope")
        if ($diagnosticScope -ne "local_only") {
            $issues.Add("diagnostic_scope is missing or not local_only")
        }
        if ($null -eq $redaction) {
            $issues.Add("support_package_redaction is missing")
        }
        if ($null -eq $externalReview) {
            $issues.Add("support_package_redaction.external_review is missing")
        }
        if ((Get-JsonValue $redaction @("public_safe")) -ne $false) {
            $issues.Add("support package public-safe flag was not false in input")
        }
        if ((Get-JsonValue $externalReview @("public_safe")) -ne $false) {
            $issues.Add("external review public-safe flag was not false in input")
        }
        if ((Get-StringJsonValue $externalReview @("status")) -ne "manual_review_required") {
            $issues.Add("external review status is not manual_review_required")
        }
        if ((Get-JsonValue $externalReview @("required_before_external_sharing")) -ne $true) {
            $issues.Add("external review is not marked required before external sharing")
        }
    }
    catch {
        $inputParseStatus = "parse_error"
        $parseError = "diagnostics package JSON could not be parsed"
        $issues.Add($parseError)
    }
}

$selectedPackageLabel = if ($null -ne $selectedPackage) { Get-DisplayPath $selectedPackage.FullName } elseif ($selectionMode -eq "specified") { Get-DisplayPath $specifiedPath } else { "" }
$selectedPackageBytes = if ($null -ne $selectedPackage) { [int64]$selectedPackage.Length } else { 0 }
$selectedPackageModified = if ($null -ne $selectedPackage) { $selectedPackage.LastWriteTimeUtc.ToString("o") } else { "" }
$summaryStatus = if (-not $inputExists) {
    "blocked_missing_diagnostics_package"
}
elseif ($inputParseStatus -ne "parsed") {
    "blocked_unreadable_diagnostics_package"
}
elseif ($issues.Count -gt 0) {
    "blocked_contract_mismatch"
}
else {
    "manual_external_review_template_ready"
}

$actualExportedPackagePathLabel = if ([string]::IsNullOrWhiteSpace($selectedPackageLabel)) {
    "uncollected"
}
else {
    $selectedPackageLabel
}
$actualExportedPackagePathStatus = if ($inputExists) {
    "path_label_recorded_only"
}
else {
    "missing"
}
$externalSharingBlockedReasons = New-Object System.Collections.Generic.List[string]
foreach ($issue in $issues) {
    $externalSharingBlockedReasons.Add($issue) | Out-Null
}
if (-not $inputExists) {
    $externalSharingBlockedReasons.Add("actual exported diagnostics package path label is missing") | Out-Null
}
if ($inputParseStatus -eq "parse_error") {
    $externalSharingBlockedReasons.Add("actual exported diagnostics package could not be parsed") | Out-Null
}
$externalSharingBlockedReasons.Add("actual exported diagnostics package content review is uncollected") | Out-Null
$externalSharingBlockedReasons.Add("reviewer identity and review timestamp are uncollected") | Out-Null

$reviewChecklist = @(
    [ordered]@{
        id = "actual_exported_package_path_label"
        status = $actualExportedPackagePathStatus
        reviewed = $false
        required = $true
        actual_exported_package_path_label = $actualExportedPackagePathLabel
        prompt = "Record the actual exported diagnostics package path label and confirm the human reviewer opened that exact package."
    },
    [ordered]@{
        id = "reviewed_logs"
        status = "pending"
        reviewed = $false
        required = $true
        prompt = "Review logs and log labels inside the actual exported package for raw errors, hostnames, usernames, tokens, local paths, or support-only values."
    },
    [ordered]@{
        id = "reviewed_path_labels"
        status = "pending"
        reviewed = $false
        required = $true
        prompt = "Review path labels and filesystem summaries in the actual exported package; full user, organization, database, cache, and log paths must not be externally shared."
    },
    [ordered]@{
        id = "reviewed_task_traces"
        status = "pending"
        reviewed = $false
        required = $true
        prompt = "Review task traces, prompts, approvals, tool calls, tool results, replay metadata, screenshots, recordings, and task evidence labels."
    },
    [ordered]@{
        id = "reviewed_model_traces"
        status = "pending"
        reviewed = $false
        required = $true
        prompt = "Review local/cloud model traces, hidden prompts, provider metadata, model messages, costs, and runtime diagnostics for support-only or private content."
    },
    [ordered]@{
        id = "reviewed_device_identifiers"
        status = "pending"
        reviewed = $false
        required = $true
        prompt = "Review device identifiers, pairing ids, grant ids, hostnames, LAN addresses, certificate labels, browser/device names, and mobile transport metadata."
    },
    [ordered]@{
        id = "reviewer_timestamp"
        status = "uncollected"
        reviewed = $false
        required = $true
        reviewer_identity_redacted = "uncollected"
        reviewed_at_utc = "uncollected"
        prompt = "Record the reviewer identity label and UTC timestamp in a separate human review artifact."
    },
    [ordered]@{
        id = "blocked_reason"
        status = "blocked"
        reviewed = $false
        required = $true
        blocked_reason_redacted = @($externalSharingBlockedReasons)
        prompt = "Keep external sharing blocked and record the reason until actual package content review is complete."
    },
    [ordered]@{
        id = "scope_and_audience"
        status = "pending"
        reviewed = $false
        required = $true
        prompt = "Confirm the package is local-only trusted-support material, not public material."
    },
    [ordered]@{
        id = "external_sharing_decision"
        status = "pending"
        reviewed = $false
        required = $true
        prompt = "Record a separate human decision before any external sharing; this helper cannot grant approval."
    }
)

$packet = [ordered]@{
    schema_version = 1
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    generated_by = "scripts/collect_diagnostics_external_review_packet.ps1"
    marker = "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF"
    outputs = [ordered]@{
        redacted_json = Get-DisplayPath $jsonPath
        redacted_markdown = Get-DisplayPath $markdownPath
    }
    readonly_scope = [ordered]@{
        reads_local_diagnostics_package_only = $true
        starts_product_processes = $false
        performs_network_requests = $false
        uploads_external_services = $false
        installs_dependencies = $false
        changes_backend_product_logic = $false
        changes_desktop_ui = $false
        changes_mobile_app = $false
        writes_only_external_review_template_artifacts = $true
    }
    redaction = [ordered]@{
        output_policy = "redacted JSON and Markdown only"
        path_policy = "workspace-relative paths or file labels only"
        raw_logs_included = $false
        package_payload_copied = $false
        secrets_or_tokens_read_intentionally = $false
        external_service_data_read = $false
    }
    review_scope = [ordered]@{
        automated_redaction_template = $true
        automated_redaction_template_scope = "collects labels, contract observations, and manual checklist fields only"
        actual_package_content_review_completed = $false
        automated_template_is_actual_package_content_review = $false
        actual_content_review_required_before_external_sharing = $true
        actual_exported_package_path_label = $actualExportedPackagePathLabel
    }
    summary = [ordered]@{
        status = $summaryStatus
        public_safe = $false
        external_sharing_allowed = $false
        claim_allowed = $false
        required_before_external_sharing = $true
        human_review_signoff = $false
        external_public_safe_signoff = $false
        template_is_human_signoff = $false
        actual_package_content_review_completed = $false
        automated_template_only = $true
        input_issue_count = $issues.Count
    }
    claim_controls = [ordered]@{
        public_safe = $false
        external_sharing_allowed = $false
        claim_allowed = $false
        helper_can_approve_public_safety = $false
        helper_can_authorize_external_sharing = $false
        actual_content_review_required = $true
        actual_content_review_completed = $false
        public_safe_approval_created = $false
    }
    input_diagnostics_package = [ordered]@{
        selection_mode = $selectionMode
        diagnostics_root = Get-DisplayPath $diagnosticsRootPath
        package_found = $inputExists
        package_label = $selectedPackageLabel
        actual_exported_package_path_label = $actualExportedPackagePathLabel
        bytes = $selectedPackageBytes
        last_write_utc = $selectedPackageModified
        parse_status = $inputParseStatus
        parse_error_redacted = $parseError
        schema_version = Get-StringJsonValue $package @("schema_version")
        generated_at = Get-StringJsonValue $package @("generated_at")
        diagnostic_scope = Get-StringJsonValue $diagnostics @("diagnostic_scope")
    }
    source_redaction_contract = [ordered]@{
        support_package_redaction_present = ($null -ne $redaction)
        external_review_present = ($null -ne $externalReview)
        package_public_safe_observation = Get-FalseObservation (Get-JsonValue $redaction @("public_safe"))
        external_review_public_safe_observation = Get-FalseObservation (Get-JsonValue $externalReview @("public_safe"))
        external_review_status = Get-StringJsonValue $externalReview @("status")
        required_before_external_sharing_observation = Get-RequiredObservation (Get-JsonValue $externalReview @("required_before_external_sharing"))
    }
    review_template = [ordered]@{
        marker = "NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF"
        template_status = "manual_external_diagnostics_review_required"
        public_safe = $false
        external_sharing_allowed = $false
        claim_allowed = $false
        required_before_external_sharing = $true
        actual_package_content_review_completed = $false
        actual_exported_package_path_label = $actualExportedPackagePathLabel
        human_decision = "pending"
        reviewer_identity_redacted = "uncollected"
        reviewed_at_utc = "uncollected"
        blocked_reason_redacted = @($externalSharingBlockedReasons)
        reviewer_notes_redacted = @()
        checklist = $reviewChecklist
        must_not_be_recorded_as = @(
            "external public-safe signoff",
            "permission to publish diagnostics",
            "human reviewer approval",
            "clean-machine diagnostics signoff",
            "release-candidate signoff"
        )
    }
    issues_redacted = @($issues)
    next_manual_evidence_needed = @(
        "Open the selected diagnostics package locally and inspect any support-only attachments separately.",
        "Record reviewer identity, timestamp, decision, and redacted notes in a separate human content-review artifact.",
        "Keep this helper output separate from the actual human review decision.",
        "Do not externally share diagnostics while public_safe remains false."
    )
}

$markdownLines = New-Object System.Collections.Generic.List[string]
$markdownLines.Add("# Diagnostics External Review Template")
$markdownLines.Add("")
$markdownLines.Add("- Marker: $($packet.marker)")
$markdownLines.Add("- Generated: $($packet.generated_at_utc)")
$markdownLines.Add("- JSON: $($packet.outputs.redacted_json)")
$markdownLines.Add("- Markdown: $($packet.outputs.redacted_markdown)")
$markdownLines.Add("- Status: $($packet.summary.status)")
$markdownLines.Add("- Package: $($packet.input_diagnostics_package.package_label)")
$markdownLines.Add("- Actual exported package path label: $($packet.input_diagnostics_package.actual_exported_package_path_label)")
$markdownLines.Add("- Scope: local diagnostics package review template only; no product process starts, no network requests, no uploads, no dependency install.")
$markdownLines.Add("- Automated redaction/template only: true")
$markdownLines.Add("- Actual package content review completed: false")
$markdownLines.Add("- Public safe: false")
$markdownLines.Add("- External sharing allowed: false")
$markdownLines.Add("- Claim allowed: false")
$markdownLines.Add("- Required before external sharing: true")
$markdownLines.Add("")
$markdownLines.Add("## Red Line")
$markdownLines.Add("- NOT_EXTERNAL_PUBLIC_SAFE_SIGNOFF")
$markdownLines.Add("- This template is automated redaction/checklist scaffolding, not actual exported package content review.")
$markdownLines.Add("- This template is not human reviewer approval and must not be treated as permission to publish or externally share diagnostics.")
$markdownLines.Add("- public_safe remains false; a separate manual content-review artifact still is not public-safe approval.")
$markdownLines.Add("- external_sharing_allowed=false and claim_allowed=false remain false even when this helper exits 0.")
$markdownLines.Add("")
$markdownLines.Add("## Reviewer And Blocker Fields")
$markdownLines.Add("- Reviewer: $($packet.review_template.reviewer_identity_redacted)")
$markdownLines.Add("- Reviewed at UTC: $($packet.review_template.reviewed_at_utc)")
$markdownLines.Add("- Blocked reasons redacted:")
foreach ($reason in $packet.review_template.blocked_reason_redacted) {
    $markdownLines.Add("  - $reason")
}
$markdownLines.Add("")
$markdownLines.Add("## Checklist")
foreach ($item in $packet.review_template.checklist) {
    $markdownLines.Add("- [$($item.status)] $($item.id): $($item.prompt)")
}
$markdownLines.Add("")
$markdownLines.Add("## Issues")
if ($packet.issues_redacted.Count -eq 0) {
    $markdownLines.Add("- none")
}
else {
    foreach ($issue in $packet.issues_redacted) {
        $markdownLines.Add("- $issue")
    }
}
$markdownLines.Add("")
$markdownLines.Add("## Must Not Be Recorded As")
foreach ($item in $packet.review_template.must_not_be_recorded_as) {
    $markdownLines.Add("- $item")
}
$markdown = $markdownLines -join "`n"

$packet | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $jsonPath -Encoding utf8
$markdown | Set-Content -LiteralPath $markdownPath -Encoding utf8

Write-Host "Diagnostics external review template"
Write-Host "Marker: $($packet.marker)"
Write-Host "Redacted JSON: $($packet.outputs.redacted_json)"
Write-Host "Redacted Markdown: $($packet.outputs.redacted_markdown)"
Write-Host "Status: $($packet.summary.status)"
Write-Host ""
Write-Host $markdown

if ($packet.summary.status -ne "manual_external_review_template_ready") {
    exit 1
}

exit 0
