param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000,
    [switch]$InstallMissingDependencies
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$DesktopDir = Join-Path $Root "desktop"
Set-Location $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Format-DependencyList([string[]]$Items) {
    return ($Items | ForEach-Object { "  - $_" }) -join "`n"
}

function Find-Python {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    throw "Python was not found. Install Python 3.12+ or create .venv in the project root."
}

function Find-Npm {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npm) {
        return $npm.Source
    }
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        return $npm.Source
    }
    throw "npm was not found. Install Node.js 20+, then run .\scripts\setup_dev.ps1."
}

function Get-MissingPythonRequirements([string]$Python) {
    $requirementsPath = Join-Path $Root "backend\requirements.txt"
    $previousRequirementsPath = $env:LENGRVIS_REQUIREMENTS_CHECK_PATH
    $dependencyCheckScript = @'
import importlib.metadata as metadata
import os
import pathlib
import platform
import re
import sys


def marker_applies(marker):
    marker = marker.strip()
    for key, actual in (
        ("platform_system", platform.system()),
        ("sys_platform", sys.platform),
    ):
        match = re.fullmatch(rf"{key}\s*(==|!=)\s*['\"]([^'\"]+)['\"]", marker)
        if match:
            operator, expected = match.groups()
            return actual == expected if operator == "==" else actual != expected
    return True


def requirement_name(line):
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("-"):
        return None
    line = re.split(r"\s+#", line, 1)[0].strip()
    requirement, _, marker = line.partition(";")
    if marker and not marker_applies(marker):
        return None
    requirement = requirement.strip()
    if "://" in requirement or requirement.startswith((".", "/")):
        return None
    return re.split(r"\s*(?:\[|===|==|~=|!=|<=|>=|<|>)", requirement, 1)[0].strip() or None


requirements_path = pathlib.Path(os.environ["LENGRVIS_REQUIREMENTS_CHECK_PATH"])
missing = []
for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
    name = requirement_name(raw_line)
    if not name:
        continue
    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        missing.append(name)

if missing:
    print("\n".join(missing))
    sys.exit(1)
'@

    try {
        $env:LENGRVIS_REQUIREMENTS_CHECK_PATH = $requirementsPath
        $output = & $Python -c $dependencyCheckScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            return @()
        }
        return @($output | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() })
    }
    finally {
        if ($null -eq $previousRequirementsPath) {
            Remove-Item Env:\LENGRVIS_REQUIREMENTS_CHECK_PATH -ErrorAction SilentlyContinue
        }
        else {
            $env:LENGRVIS_REQUIREMENTS_CHECK_PATH = $previousRequirementsPath
        }
    }
}

function Install-DevelopmentDependencies([string]$Python) {
    # Product startup deliberately avoids installs; this explicit setup path may mutate the workspace.
    $setupScript = Join-Path $PSScriptRoot "setup_dev.ps1"
    & $setupScript
}

function Ensure-PythonDependencies([string]$Python) {
    $missingDependencies = @(Get-MissingPythonRequirements $Python)
    if ($missingDependencies.Count -eq 0) {
        return
    }

    $missingList = Format-DependencyList $missingDependencies
    throw @"
Missing backend Python dependencies:
$missingList

Product startup does not install dependencies in place.
For a development setup, run:
  .\scripts\setup_dev.ps1
Or install them manually:
  $Python -m pip install -r requirements-dev.txt
"@
}

$env:LENGRVIS_ENV = if ($env:LENGRVIS_ENV) { $env:LENGRVIS_ENV } elseif ($env:LENGRVIS_ENV) { $env:LENGRVIS_ENV } elseif ($env:LENGRVIS_ENV) { $env:LENGRVIS_ENV } else { "development" }
$env:LENGRVIS_ENV = $env:LENGRVIS_ENV
$env:LENGRVIS_FULL_BACKEND = "1"

$Python = Find-Python
if ($InstallMissingDependencies) {
    Install-DevelopmentDependencies $Python
}
else {
    Ensure-PythonDependencies $Python
}

$Candidates = @(
    "backend.main:full_app",
    "backend.api:app",
    "lengrvis.main:app",
    "lengrvis.api:app"
)

foreach ($App in $Candidates) {
    & $Python -c "import importlib; module, attr = '$App'.split(':'); assert hasattr(importlib.import_module(module), attr)" *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Starting backend with $App on http://$BackendHost`:$BackendPort"
        & $Python -m uvicorn $App --reload --host $BackendHost --port $BackendPort
        exit $LASTEXITCODE
    }
}

Write-Warning "No backend ASGI app found yet. Expected one of: $($Candidates -join ', ')"
Write-Host "Install development dependencies with .\scripts\setup_dev.ps1, then run scripts\test.ps1 to verify scaffolding."
