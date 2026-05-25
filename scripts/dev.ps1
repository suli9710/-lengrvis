param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

$env:MAVRIS_ENV = if ($env:MAVRIS_ENV) { $env:MAVRIS_ENV } else { "development" }

$Candidates = @(
    "backend.main:app",
    "backend.api:app",
    "mavris.main:app",
    "mavris.api:app"
)

foreach ($App in $Candidates) {
    python -c "import importlib; module, attr = '$App'.split(':'); assert hasattr(importlib.import_module(module), attr)" *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Starting backend with $App on http://$BackendHost`:$BackendPort"
        python -m uvicorn $App --reload --host $BackendHost --port $BackendPort
        exit $LASTEXITCODE
    }
}

Write-Warning "No backend ASGI app found yet. Expected one of: $($Candidates -join ', ')"
Write-Host "Install dependencies, then run scripts\test.ps1 to verify scaffolding."
