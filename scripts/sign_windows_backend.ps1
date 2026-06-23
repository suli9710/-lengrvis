param(
    [string]$BackendExe = "dist\backend.exe",
    [switch]$SkipModuleInstall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Require-Env {
    param([string[]]$Names)
    $missing = @()
    foreach ($name in $Names) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($value) -or $value.Trim().StartsWith("REPLACE_")) {
            $missing += $name
        }
    }
    if ($missing.Count -gt 0) {
        throw "Missing Azure Trusted Signing environment variables: $($missing -join ', ')"
    }
}

$requiredEnv = @(
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_TRUSTED_SIGNING_ENDPOINT",
    "AZURE_TRUSTED_SIGNING_ACCOUNT_NAME",
    "AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME"
)
Require-Env -Names $requiredEnv

$backendPath = Resolve-ProjectPath $BackendExe
if (-not (Test-Path -LiteralPath $backendPath -PathType Leaf)) {
    throw "Missing backend executable to sign: $backendPath"
}

$isWindowsHost = $env:OS -eq "Windows_NT" -or [string][System.IO.Path]::DirectorySeparatorChar -eq "\"
if (-not $isWindowsHost) {
    throw "Windows backend signing must run on Windows because it verifies Authenticode signatures."
}

if (-not $SkipModuleInstall) {
    try {
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser | Out-Null
    }
    catch {
        Write-Host "NuGet package provider installation skipped or already satisfied: $($_.Exception.Message)"
    }
    Install-Module -Name TrustedSigning -MinimumVersion 0.5.0 -Force -Repository PSGallery -Scope CurrentUser
}

$timestampUrl = [Environment]::GetEnvironmentVariable("AZURE_TRUSTED_SIGNING_TIMESTAMP_RFC3161")
if ([string]::IsNullOrWhiteSpace($timestampUrl)) {
    $timestampUrl = "http://timestamp.acs.microsoft.com"
}

Write-Host "Signing backend executable with Azure Trusted Signing: $backendPath"
Invoke-TrustedSigning `
    -Endpoint $env:AZURE_TRUSTED_SIGNING_ENDPOINT `
    -CertificateProfileName $env:AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME `
    -CodeSigningAccountName $env:AZURE_TRUSTED_SIGNING_ACCOUNT_NAME `
    -TimestampRfc3161 $timestampUrl `
    -TimestampDigest "SHA256" `
    -FileDigest "SHA256" `
    -Files $backendPath

$signature = Get-AuthenticodeSignature -LiteralPath $backendPath
if ($signature.Status -ne "Valid") {
    throw "Backend executable signing failed Authenticode verification: $($signature.Status)"
}

Write-Host "Backend executable signature verified: $backendPath"
