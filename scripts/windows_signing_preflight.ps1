param(
    [string]$OutputDir = ".tmp\windows-signing-preflight"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Test-ConfiguredEnv {
    param([string]$Name)
    $value = [Environment]::GetEnvironmentVariable($Name)
    return -not [string]::IsNullOrWhiteSpace($value) -and -not $value.Trim().StartsWith("REPLACE_")
}

function Get-ArtifactStatus {
    param([string]$Path)
    $fullPath = Join-Path $Root $Path
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        return [ordered]@{
            path = $Path
            exists = $false
            authenticode_status = "missing"
            size_bytes = 0
        }
    }
    $item = Get-Item -LiteralPath $fullPath
    $authenticodeStatus = "check_unavailable"
    try {
        $signature = Get-AuthenticodeSignature -LiteralPath $fullPath -ErrorAction Stop
        $authenticodeStatus = $signature.Status.ToString()
    }
    catch {
        $authenticodeStatus = "check_unavailable"
    }
    return [ordered]@{
        path = $Path
        exists = $true
        authenticode_status = $authenticodeStatus
        size_bytes = $item.Length
    }
}

$azureEnv = @(
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_TRUSTED_SIGNING_ENDPOINT",
    "AZURE_TRUSTED_SIGNING_ACCOUNT_NAME",
    "AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME",
    "AZURE_TRUSTED_SIGNING_PUBLISHER_NAME"
)

$azureStatus = [ordered]@{}
foreach ($name in $azureEnv) {
    $azureStatus[$name] = Test-ConfiguredEnv $name
}

$pfxEnv = [ordered]@{
    WIN_CSC_LINK = Test-ConfiguredEnv "WIN_CSC_LINK"
    WIN_CSC_KEY_PASSWORD = Test-ConfiguredEnv "WIN_CSC_KEY_PASSWORD"
}

$version = (Get-Content -LiteralPath (Join-Path $Root "desktop\package.json") -Raw | ConvertFrom-Json).version
$artifacts = @(
    "dist\backend.exe",
    "dist\Lengrvis-win-portable\resources\backend\backend.exe",
    "dist\Lengrvis-win-portable\Lengrvis.exe",
    "dist\Lengrvis-$version-x64-self-extracting.exe"
)

$targetStatuses = @()
foreach ($artifact in $artifacts) {
    $targetStatuses += Get-ArtifactStatus $artifact
}

$trustedSigningModule = Get-Module -ListAvailable TrustedSigning | Sort-Object Version -Descending | Select-Object -First 1
$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue

$missingAzure = @($azureEnv | Where-Object { -not $azureStatus[$_] })
$missingPfx = @($pfxEnv.Keys | Where-Object { -not $pfxEnv[$_] })
$missingTargets = @($targetStatuses | Where-Object { -not $_.exists } | ForEach-Object { $_.path })
$unsignedTargets = @($targetStatuses | Where-Object { $_.exists -and $_.authenticode_status -ne "Valid" } | ForEach-Object { $_.path })

$azureReadyToAttempt = $missingAzure.Count -eq 0 -and $missingTargets.Count -eq 0
$pfxReadyToAttempt = $missingPfx.Count -eq 0 -and $missingTargets.Count -eq 0 -and $null -ne $signtool
$readyToSign = $azureReadyToAttempt -or $pfxReadyToAttempt
$alreadySigned = $missingTargets.Count -eq 0 -and $unsignedTargets.Count -eq 0
$blockers = @()
if ($missingTargets.Count -gt 0) {
    $blockers += "missing signing target artifacts"
}
if ($missingAzure.Count -gt 0 -and $missingPfx.Count -gt 0) {
    $blockers += "no complete Azure Trusted Signing or PFX signing credentials are configured"
}
if ($missingPfx.Count -eq 0 -and $null -eq $signtool) {
    $blockers += "PFX credentials are present but signtool.exe is not available"
}
if ($unsignedTargets.Count -gt 0) {
    $blockers += "one or more target artifacts are unsigned or invalid"
}

$payload = [ordered]@{
    schema_version = 1
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    repository = $Root
    mode = "preflight_only_not_release_signoff"
    windows_host = ($env:OS -eq "Windows_NT" -or [System.IO.Path]::DirectorySeparatorChar -eq "\")
    azure_trusted_signing_env_present = $azureStatus
    pfx_env_present = $pfxEnv
    trusted_signing_module = if ($trustedSigningModule) {
        [ordered]@{
            available = $true
            version = $trustedSigningModule.Version.ToString()
            path = $trustedSigningModule.Path
        }
    } else {
        [ordered]@{
            available = $false
            version = ""
            path = ""
        }
    }
    signtool = if ($signtool) {
        [ordered]@{
            available = $true
            path = $signtool.Source
        }
    } else {
        [ordered]@{
            available = $false
            path = ""
        }
    }
    target_artifacts = $targetStatuses
    missing_azure_env = $missingAzure
    missing_pfx_env = $missingPfx
    missing_artifacts = $missingTargets
    unsigned_artifacts = $unsignedTargets
    blockers = $blockers
    ready_to_attempt_azure_signing = $azureReadyToAttempt
    ready_to_attempt_pfx_signing = $pfxReadyToAttempt
    ready_to_attempt_any_signing = $readyToSign
    all_targets_already_authenticode_valid = $alreadySigned
    next_actions = @(
        "For Azure Trusted Signing, configure all AZURE_TRUSTED_SIGNING_* and Azure identity variables, then run npm run sign:windows:release.",
        "For OV/EV PFX signing, configure WIN_CSC_LINK and WIN_CSC_KEY_PASSWORD for electron-builder and ensure signtool.exe is on PATH; backend/portable helper scripts currently use Azure Trusted Signing.",
        "Rebuild missing artifacts with scripts\\build_all.ps1 before signing.",
        "Never paste certificate private keys or secret values into evidence files."
    )
    claim_controls = [ordered]@{
        signs_files = $false
        contains_secret_values = $false
        release_signoff = $false
        signed_release_claim_allowed = $alreadySigned -and $blockers.Count -eq 0
    }
}

$outDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $Root $OutputDir }
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$jsonPath = Join-Path $outDir "windows-signing-preflight.json"
$payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

Write-Host "Windows signing preflight written to $jsonPath"
if ($missingAzure.Count -gt 0) {
    Write-Host "Missing Azure Trusted Signing environment variables: $($missingAzure -join ', ')"
}
if ($missingPfx.Count -gt 0) {
    Write-Host "Missing PFX signing environment variables: $($missingPfx -join ', ')"
}
if ($missingTargets.Count -gt 0) {
    Write-Host "Missing signing target artifacts: $($missingTargets -join ', ')"
}
if ($unsignedTargets.Count -gt 0) {
    Write-Host "Unsigned or invalid signing target artifacts: $($unsignedTargets -join ', ')"
}
if ($blockers.Count -gt 0) {
    Write-Host "Signing blockers: $($blockers -join '; ')"
}

if ($blockers.Count -gt 0) {
    exit 1
}
