param(
    [switch]$WithDeps
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

python -m pip install -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$InstallArgs = @("install", "chromium")
if ($WithDeps) {
    $InstallArgs = @("install", "--with-deps", "chromium")
}

python -m playwright @InstallArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
