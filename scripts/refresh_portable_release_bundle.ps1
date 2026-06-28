param(
    [string]$PortableDir = "dist\Lengrvis-win-portable",
    [string]$PortableZip = "dist\Lengrvis-win-portable.zip",
    [string]$SelfExtractingExe = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

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
$PortableZipPath = Resolve-ProjectPath $PortableZip
$SelfExtractingPath = Resolve-ProjectPath $SelfExtractingExe
$PortableLauncher = Join-Path $PortablePath "Lengrvis.exe"

if (-not (Test-Path -LiteralPath $PortableLauncher -PathType Leaf)) {
    throw "Portable launcher was not found at $PortableLauncher. Run scripts\build_portable.ps1 first."
}

$PortableZipParent = Split-Path -Parent $PortableZipPath
if ($PortableZipParent) {
    New-Item -ItemType Directory -Path $PortableZipParent -Force | Out-Null
}
if (Test-Path -LiteralPath $PortableZipPath) {
    Remove-Item -LiteralPath $PortableZipPath -Force
}
Compress-Archive -Path (Join-Path $PortablePath "*") -DestinationPath $PortableZipPath -CompressionLevel Optimal
Write-Host "Refreshed portable zip: $PortableZipPath"

$SelfExtractingParent = Split-Path -Parent $SelfExtractingPath
if ($SelfExtractingParent) {
    New-Item -ItemType Directory -Path $SelfExtractingParent -Force | Out-Null
}
& "$PSScriptRoot\create_csharp_self_extracting_exe.ps1" -PortableZip $PortableZip -OutputExe $SelfExtractingExe
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Refreshed self-extracting executable: $SelfExtractingPath"
