param(
    [switch]$Coverage,
    [string[]]$PytestArgs = @()
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

$ArgsList = @("backend/tests")
if ($Coverage) {
    $ArgsList += @("--cov=backend", "--cov-report=term-missing")
}
$ArgsList += $PytestArgs

python -m pytest @ArgsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path "desktop\package.json") {
    npm --prefix desktop run typecheck
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

