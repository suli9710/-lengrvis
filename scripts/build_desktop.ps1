$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (-not (Test-Path "desktop\package.json")) {
    throw "desktop/package.json was not found."
}

npm --prefix desktop install
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

npm --prefix desktop run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

npm --prefix desktop run dist
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

