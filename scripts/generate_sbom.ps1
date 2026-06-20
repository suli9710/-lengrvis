[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $resolvedRoot ".tmp\sbom\lengrvis-sbom.cdx.json"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputPath)) {
    $OutputPath = Join-Path $resolvedRoot $OutputPath
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    throw "python is required to generate the SBOM."
}

& $python.Source (Join-Path $PSScriptRoot "generate_sbom.py") --root $resolvedRoot --output $OutputPath
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
