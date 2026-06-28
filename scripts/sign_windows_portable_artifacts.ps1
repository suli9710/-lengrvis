param(
    [string]$PortableDir = "dist\Lengrvis-win-portable",
    [string]$SelfExtractingExe = "",
    [switch]$LauncherOnly,
    [switch]$SelfExtractingOnly,
    [switch]$SkipModuleInstall
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if ($LauncherOnly -and $SelfExtractingOnly) {
    throw "Cannot specify both -LauncherOnly and -SelfExtractingOnly."
}

. "$PSScriptRoot\sign_windows_trusted_signing.ps1"

function Get-DesktopVersion {
    $packageJsonPath = Join-Path $Root "desktop\package.json"
    $package = Get-Content -LiteralPath $packageJsonPath -Raw | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace($package.version)) {
        throw "desktop\package.json has no version field; it is the single source of truth for artifact names."
    }
    return $package.version
}

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

if ([string]::IsNullOrWhiteSpace($SelfExtractingExe)) {
    $SelfExtractingExe = "dist\Lengrvis-$(Get-DesktopVersion)-x64-self-extracting.exe"
}

$PortablePath = Resolve-ProjectPath $PortableDir
$SelfExtractingPath = Resolve-ProjectPath $SelfExtractingExe
$PortableLauncher = Join-Path $PortablePath "Lengrvis.exe"

$targets = @()
if (-not $SelfExtractingOnly) {
    if (Test-Path -LiteralPath $PortableLauncher -PathType Leaf) {
        $targets += $PortableLauncher
    }
}
if (-not $LauncherOnly) {
    if (Test-Path -LiteralPath $SelfExtractingPath -PathType Leaf) {
        $targets += $SelfExtractingPath
    }
}

if ($targets.Count -eq 0) {
  $expected = @()
  if (-not $SelfExtractingOnly) { $expected += $PortableLauncher }
  if (-not $LauncherOnly) { $expected += $SelfExtractingPath }
  throw "No portable release executables found to sign. Expected: $($expected -join ', ')."
}

Invoke-TrustedWindowsSigning -Files $targets -SkipModuleInstall:$SkipModuleInstall
