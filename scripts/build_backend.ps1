$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

python -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller..."
    python -m pip install pyinstaller
}

python backend\build_backend.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

