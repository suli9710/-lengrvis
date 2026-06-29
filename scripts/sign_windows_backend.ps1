param(
    [string]$BackendExe = "dist\backend.exe",
    [switch]$SkipModuleInstall,
    [switch]$AllowModuleInstall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

. "$PSScriptRoot\sign_windows_trusted_signing.ps1"

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

$backendPath = Resolve-ProjectPath $BackendExe
Invoke-TrustedWindowsSigning -Files @($backendPath) -SkipModuleInstall:$SkipModuleInstall -AllowModuleInstall:$AllowModuleInstall

$portableBackendPath = Join-Path $Root "dist\Lengrvis-win-portable\resources\backend\backend.exe"
if (Test-Path -LiteralPath (Split-Path -Parent $portableBackendPath) -PathType Container) {
    Copy-Item -LiteralPath $backendPath -Destination $portableBackendPath -Force
    $portableSignature = Get-AuthenticodeSignature -LiteralPath $portableBackendPath
    if ($portableSignature.Status -ne "Valid") {
        throw "Portable backend copy failed Authenticode verification: $($portableSignature.Status)"
    }
    Write-Host "Refreshed signed backend in portable tree: $portableBackendPath"
}
