if ([string]::IsNullOrWhiteSpace($PSCommandPath)) {
    $releaseEvidenceLibRoot = $PSScriptRoot
}
else {
    $releaseEvidenceLibRoot = Split-Path -Parent $PSCommandPath
}

. (Join-Path $releaseEvidenceLibRoot "release_evidence_paths.ps1")
. (Join-Path $releaseEvidenceLibRoot "release_evidence_redaction.ps1")
. (Join-Path $releaseEvidenceLibRoot "release_evidence_json.ps1")
. (Join-Path $releaseEvidenceLibRoot "release_evidence_mobile_json.ps1")
. (Join-Path $releaseEvidenceLibRoot "release_evidence_local_model_json.ps1")
. (Join-Path $releaseEvidenceLibRoot "release_evidence_artifacts.ps1")

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
