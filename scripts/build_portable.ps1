param(
    [string]$OutputDir = "dist\Mavris-win-portable"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$ElectronDist = Join-Path $Root "desktop\node_modules\electron\dist"
$DesktopDist = Join-Path $Root "desktop\dist"
$BackendExe = Join-Path $Root "dist\backend.exe"
$Out = Join-Path $Root $OutputDir

if (-not (Test-Path $ElectronDist)) {
    throw "Electron runtime was not found at $ElectronDist. Run npm --prefix desktop install first."
}

if (-not (Test-Path $DesktopDist)) {
    throw "Desktop build was not found at $DesktopDist. Run npm --prefix desktop run build first."
}

if (-not (Test-Path $BackendExe)) {
    throw "Backend executable was not found at $BackendExe. Run scripts\build_backend.ps1 first."
}

if (Test-Path $Out) {
    $Resolved = Resolve-Path -LiteralPath $Out
    if ($Resolved.Path -notlike "$Root*") {
        throw "Refusing to remove output outside project root: $($Resolved.Path)"
    }
    Remove-Item -LiteralPath $Resolved.Path -Recurse -Force
}

New-Item -ItemType Directory -Path $Out | Out-Null
Copy-Item -Path (Join-Path $ElectronDist "*") -Destination $Out -Recurse -Force

$ElectronExe = Join-Path $Out "electron.exe"
$MavrisExe = Join-Path $Out "Mavris.exe"
if (Test-Path $MavrisExe) {
    Remove-Item -LiteralPath $MavrisExe -Force
}
Rename-Item -LiteralPath $ElectronExe -NewName "Mavris.exe"

$Resources = Join-Path $Out "resources"
$AppDir = Join-Path $Resources "app"
$AppDistDir = Join-Path $AppDir "dist"
$BackendDir = Join-Path $Resources "backend"
New-Item -ItemType Directory -Path $AppDistDir -Force | Out-Null
New-Item -ItemType Directory -Path $BackendDir -Force | Out-Null

Copy-Item -Path (Join-Path $DesktopDist "*") -Destination $AppDistDir -Recurse -Force
Copy-Item -Path (Join-Path $Root "desktop\package.json") -Destination $AppDir -Force
Copy-Item -Path $BackendExe -Destination (Join-Path $BackendDir "backend.exe") -Force

Write-Host "Portable build created at $Out"
