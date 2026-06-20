[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$EvidenceRoot = "",
    [string]$CandidateCommit = "",
    [string]$BuildIdentifier = "",
    [string]$InstallerArtifactLabel = "",
    [string]$InstallerSha256 = "",
    [string]$SigningSubject = "",
    [string]$SigningThumbprint = "",
    [string]$CleanWindowsMachineLabel = "",
    [string]$UpgradeFromVersion = "",
    [string]$UpgradeToVersion = "",
    [string]$UpgradeOutcome = "",
    [string]$RollbackOutcome = "",
    [string]$RealDeviceEvidenceLabel = "",
    [string]$Reviewer = "",
    [string]$ReviewedAtUtc = "",
    [string]$BlockedReason = ""
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

if ([string]::IsNullOrWhiteSpace($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $resolvedRoot ".tmp\distribution-release-evidence-template"
}
elseif (-not [System.IO.Path]::IsPathRooted($EvidenceRoot)) {
    $EvidenceRoot = Join-Path $resolvedRoot $EvidenceRoot
}

if ([string]::IsNullOrWhiteSpace($ReviewedAtUtc)) {
    $ReviewedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
}

function Get-GitCommit {
    if (-not [string]::IsNullOrWhiteSpace($CandidateCommit)) {
        return $CandidateCommit.Trim()
    }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($null -eq $git) {
        return "unknown"
    }
    Push-Location $resolvedRoot
    try {
        $sha = & $git.Source rev-parse HEAD 2>$null
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($sha)) {
            return ([string]$sha).Trim()
        }
    }
    finally {
        Pop-Location
    }
    return "unknown"
}

function New-Check([string]$Id, [string]$Description, [string]$Evidence, $Passed) {
    $recorded = $false
    if ($Passed -is [array]) {
        $recorded = [bool](@($Passed | Where-Object { [bool]$_ }).Count -gt 0)
    }
    else {
        $recorded = [bool]$Passed
    }
    $result = "uncollected"
    if ($recorded) {
        $result = "recorded"
    }
    return [ordered]@{
        id = $Id
        description = $Description
        evidence = $Evidence
        result = $result
        passed = $false
    }
}

function Has-Value([string]$Value) {
    return [bool](-not [string]::IsNullOrWhiteSpace($Value))
}

$commit = Get-GitCommit
$runRoot = Join-Path $EvidenceRoot ("run-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null

$checks = @()
$checks += New-Check "clean_windows_machine" "Fresh Windows machine/profile install was executed." $CleanWindowsMachineLabel (Has-Value $CleanWindowsMachineLabel)
$checks += New-Check "signed_installer" "Installer artifact signature was verified with release certificate metadata." "$SigningSubject $SigningThumbprint" ((Has-Value $SigningSubject) -and (Has-Value $SigningThumbprint))
$checks += New-Check "installer_hash" "Installer artifact hash was recorded for the candidate." "$InstallerArtifactLabel $InstallerSha256" ((Has-Value $InstallerArtifactLabel) -and (Has-Value $InstallerSha256))
$checks += New-Check "upgrade_path" "Upgrade from previous version to candidate was executed and reviewed." "$UpgradeFromVersion -> $UpgradeToVersion; $UpgradeOutcome" ((Has-Value $UpgradeFromVersion) -and (Has-Value $UpgradeToVersion) -and (Has-Value $UpgradeOutcome))
$checks += New-Check "rollback_path" "Rollback from candidate to previous version was executed and reviewed." $RollbackOutcome (Has-Value $RollbackOutcome)
$checks += New-Check "real_device" "Real phone/emulator evidence was reviewed for mobile pairing and remote operation claims." $RealDeviceEvidenceLabel (Has-Value $RealDeviceEvidenceLabel)
$checks += New-Check "reviewer" "Release reviewer and review timestamp were recorded." "$Reviewer $ReviewedAtUtc" (Has-Value $Reviewer)

$missing = @($checks | Where-Object { $_.result -ne "recorded" } | ForEach-Object { $_.id })
$blocked = $missing.Count -gt 0 -or -not [string]::IsNullOrWhiteSpace($BlockedReason)
$signingStatus = "uncollected"
if ((Has-Value $SigningSubject) -and (Has-Value $SigningThumbprint)) {
    $signingStatus = "metadata_recorded_not_verified_by_template"
}
$summaryStatus = "fields_recorded_template_only"
if ($blocked) {
    $summaryStatus = "blocked_or_template_only"
}

$packet = [ordered]@{
    schema_version = 1
    generated_by = "scripts/collect_distribution_release_evidence_template.ps1"
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    artifact_type = "distribution-release-evidence-template"
    candidate = [ordered]@{
        commit = $commit
        build_identifier = $BuildIdentifier
        installer_artifact_label = $InstallerArtifactLabel
        installer_sha256 = $InstallerSha256
    }
    signing = [ordered]@{
        subject = $SigningSubject
        thumbprint = $SigningThumbprint
        status = $signingStatus
    }
    checks = $checks
    summary = [ordered]@{
        status = $summaryStatus
        claim_allowed = $false
        release_signoff = $false
        clean_windows_pass = $false
        signed_installer_pass = $false
        upgrade_pass = $false
        rollback_pass = $false
        real_device_pass = $false
        missing_fields = $missing
        blocked_reason = $BlockedReason
        reviewer = $Reviewer
        reviewed_at_utc = $ReviewedAtUtc
    }
    must_not_claim = @(
        "clean Windows pass",
        "signed installer pass",
        "upgrade pass",
        "rollback pass",
        "real-device pass",
        "release sign-off"
    )
}

$jsonPath = Join-Path $runRoot "distribution-release-evidence.redacted.template.json"
$mdPath = Join-Path $runRoot "distribution-release-evidence.redacted.template.md"
$packet | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# Distribution Release Evidence Template")
$lines.Add("")
$lines.Add("- Candidate commit: $commit")
$lines.Add("- Build identifier: $BuildIdentifier")
$lines.Add("- Status: $($packet.summary.status)")
$lines.Add("- Claim allowed: false")
$lines.Add("- Release sign-off: false")
$lines.Add("")
$lines.Add("## Checks")
$lines.Add("")
foreach ($check in $checks) {
    $lines.Add("- [$($check.result)] $($check.id): $($check.description)")
}
$lines.Add("")
$lines.Add("## Missing Fields")
$lines.Add("")
if ($missing.Count -eq 0) {
    $lines.Add("- None recorded by template fields, but this file still requires release-owner review and attached logs.")
}
else {
    foreach ($field in $missing) {
        $lines.Add("- $field")
    }
}
$lines.Add("")
$lines.Add("## Must Not Claim")
$lines.Add("")
foreach ($claim in $packet.must_not_claim) {
    $lines.Add("- $claim")
}
$lines.Add("")
[System.IO.File]::WriteAllText($mdPath, ($lines -join "`n"), (New-Object System.Text.UTF8Encoding $false))

Write-Host "Distribution release evidence template created:"
Write-Host "JSON: $jsonPath"
Write-Host "Markdown: $mdPath"
Write-Host "This template is fail-closed and is not a clean-machine, signed-installer, upgrade, rollback, real-device, or release pass."
