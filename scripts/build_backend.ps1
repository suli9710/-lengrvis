param(
    [string]$DistDir = "dist"
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

function Get-CanonicalPath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

$DistPath = Resolve-ProjectPath $DistDir
$DefaultDistPath = Join-Path $Root "dist"
$DefaultBackendExe = Join-Path $DefaultDistPath "backend.exe"
$TargetBackendExe = Join-Path $DistPath "backend.exe"
$BuildRequirementsPath = Join-Path $Root "backend\requirements-build.txt"

if (-not (Test-Path -LiteralPath $BuildRequirementsPath -PathType Leaf)) {
    throw "Missing pinned backend build dependency file: $BuildRequirementsPath"
}

Write-Host "Ensuring pinned backend build dependencies from $BuildRequirementsPath..."
python -m pip install -r $BuildRequirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install pinned backend build dependencies. Run: python -m pip install -r backend\requirements-build.txt"
}

python backend\build_backend.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not (Test-Path -LiteralPath $DefaultBackendExe -PathType Leaf)) {
    throw "Backend executable was not created at $DefaultBackendExe"
}

if ((Get-CanonicalPath $DistPath) -ne (Get-CanonicalPath $DefaultDistPath)) {
    New-Item -ItemType Directory -Path $DistPath -Force | Out-Null
    Copy-Item -LiteralPath $DefaultBackendExe -Destination $TargetBackendExe -Force
    Write-Host "Backend executable copied to $TargetBackendExe"
}
