param(
    [ValidateSet("stop", "uninstall", "remove", "query")]
    [string]$Action = "uninstall",
    [int]$WaitSeconds = 30,
    [switch]$NoStop
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
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
    throw "Python was not found. Install Python 3.11+ or create .venv first."
}

function Test-Pywin32([string]$Python) {
    & $Python -c "import win32serviceutil, win32service, win32event, servicemanager" *> $null
    return $LASTEXITCODE -eq 0
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$IsWindowsPlatform = ($PSVersionTable.PSEdition -eq "Desktop") -or $IsWindows
if (-not $IsWindowsPlatform) {
    throw "Mavris Windows Service can only be removed or controlled on Windows."
}

Set-Location $Root
$pythonExe = Find-Python
if (-not (Test-Pywin32 $pythonExe)) {
    throw "pywin32 is required. Install backend dependencies first: $pythonExe -m pip install -r backend\requirements.txt"
}

if ($Action -eq "query") {
    Write-Step "Querying service"
    & $pythonExe -m backend.service_wrapper query
    exit $LASTEXITCODE
}

if (-not (Test-Admin)) {
    throw "Administrator privileges are required for Windows Service removal/control. Re-run PowerShell as Administrator."
}

$env:MARVIS_CONFIG_DIR = $Root

if ($Action -eq "stop") {
    Write-Step "Stopping service"
    & $pythonExe -m backend.service_wrapper stop --wait $WaitSeconds
    exit $LASTEXITCODE
}

if (-not $NoStop) {
    Write-Step "Stopping service if it is running"
    & $pythonExe -m backend.service_wrapper stop --wait $WaitSeconds
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Stop failed or service was not running; continuing with removal."
    }
}

Write-Step "Removing service"
& $pythonExe -m backend.service_wrapper remove
exit $LASTEXITCODE
