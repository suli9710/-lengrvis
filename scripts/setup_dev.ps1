param(
    [switch]$SkipPython,
    [switch]$SkipDesktop,
    [switch]$SkipMobile,
    [switch]$UseSystemPython
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

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$DesktopDir = Join-Path $Root "desktop"
$MobileDir = Join-Path $Root "mobile"
Set-Location $Root

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-SystemPython {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    throw "Python was not found. Install Python 3.12+, then rerun scripts\setup_dev.ps1."
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
    throw "npm was not found. Install Node.js 20+, then rerun scripts\setup_dev.ps1."
}

function Assert-PythonVersion([string]$Python) {
    $version = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not check Python version: $Python"
    }
    if ([version]$version -lt [version]"3.12") {
        throw "Python version is too old: $version. Source development requires Python 3.12+."
    }
}

function Resolve-DevPython {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if ((Test-Path $venvPython) -and -not $UseSystemPython) {
        Assert-PythonVersion $venvPython
        return $venvPython
    }

    $python = Find-SystemPython
    Assert-PythonVersion $python
    if ($UseSystemPython) {
        return $python
    }

    Write-Step "Creating Python virtual environment: .venv"
    & $python -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create .venv. Check the Python installation and retry."
    }
    if (-not (Test-Path $venvPython)) {
        throw "Python was still missing after creating .venv: $venvPython"
    }
    return $venvPython
}

function Invoke-Checked([string]$FailureMessage, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

if (-not $SkipPython) {
    $python = Resolve-DevPython
    $requirementsPath = Join-Path $Root "requirements-dev-lock.txt"

    Write-Step "Installing Python development dependencies"
    Invoke-Checked "Failed to upgrade pip. See the output above." { & $python -m pip install -U pip }
    Invoke-Checked "Failed to install Python development dependencies. See the output above." { & $python -m pip install --require-hashes -r $requirementsPath }
}

if (-not $SkipDesktop) {
    $npm = Find-Npm
    $lockFile = Join-Path $DesktopDir "package-lock.json"

    Write-Step "Installing desktop/frontend development dependencies"
    if (Test-Path $lockFile) {
        Invoke-Checked "Failed to install desktop/frontend dependencies. See the output above." { & $npm --prefix $DesktopDir ci }
    }
    else {
        Invoke-Checked "Failed to install desktop/frontend dependencies. See the output above." { & $npm --prefix $DesktopDir install }
    }
}

if (-not $SkipMobile -and (Test-Path (Join-Path $MobileDir "package.json"))) {
    $npm = Find-Npm
    $lockFile = Join-Path $MobileDir "package-lock.json"

    Write-Step "Installing mobile development dependencies"
    if (Test-Path $lockFile) {
        Invoke-Checked "Failed to install mobile dependencies. See the output above." { & $npm --prefix $MobileDir ci }
    }
    else {
        Invoke-Checked "Failed to install mobile dependencies. See the output above." { & $npm --prefix $MobileDir install }
    }
}

Write-Host ""
Write-Host "Development setup complete." -ForegroundColor Green
Write-Host "Next steps:" -ForegroundColor Green
Write-Host "  Full app: .\Start-Lengrvis.cmd"
Write-Host "  Backend only: .\scripts\dev.ps1"
