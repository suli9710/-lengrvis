param(
    [string]$Workspace = ".tmp\ollama-bundle-smoke"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Resolve-SmokePath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Invoke-BundleOllama {
    param([string[]]$Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\bundle_ollama.ps1" @Arguments 2>&1
        $script:LastBundleOllamaExitCode = $LASTEXITCODE
        return $output
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Assert-Passes {
    param(
        [string]$Label,
        [string[]]$Arguments
    )
    $output = Invoke-BundleOllama -Arguments $Arguments
    if ($script:LastBundleOllamaExitCode -ne 0) {
        throw "$Label should have passed, but failed:`n$output"
    }
    Write-Host "[ok] $Label"
}

function Assert-FailsWith {
    param(
        [string]$Label,
        [string[]]$Arguments,
        [string]$Expected
    )
    $output = Invoke-BundleOllama -Arguments $Arguments
    if ($script:LastBundleOllamaExitCode -eq 0) {
        throw "$Label should have failed, but passed."
    }
    $text = $output | Out-String
    if ($text -notmatch [regex]::Escape($Expected)) {
        throw "$Label failed with unexpected output. Expected '$Expected'. Got:`n$text"
    }
    Write-Host "[ok] $Label"
}

$workspacePath = Resolve-SmokePath $Workspace
if (Test-Path -LiteralPath $workspacePath) {
    Remove-Item -LiteralPath $workspacePath -Recurse -Force
}
New-Item -ItemType Directory -Path $workspacePath -Force | Out-Null

$runtime = Join-Path $workspacePath "input-runtime"
$models = Join-Path $workspacePath "input-models"
$modelManifest = Join-Path $models "manifests\registry.ollama.ai\library\qwen2.5\3b"
$bundleOut = Join-Path $workspacePath "bundle"

New-Item -ItemType Directory -Path $runtime,(Split-Path -Parent $modelManifest) -Force | Out-Null
Set-Content -LiteralPath (Join-Path $runtime "ollama.exe") -Value "fake runtime" -Encoding ASCII
Set-Content -LiteralPath $modelManifest -Value "{}" -Encoding ASCII

Assert-Passes -Label "explicit runtime and model bundle" -Arguments @(
    "-AcceptLicenses",
    "-OllamaRuntimeDir", $runtime,
    "-OllamaModelsDir", $models,
    "-OutputRoot", $bundleOut,
    "-RuntimeSource", "smoke-runtime",
    "-ModelSource", "smoke-models",
    "-Model", "qwen2.5:3b"
)

$manifestPath = Join-Path $bundleOut "ollama-bundle-manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$manifestBytes = [System.IO.File]::ReadAllBytes($manifestPath)
if ($manifestBytes.Length -ge 3 -and $manifestBytes[0] -eq 0xEF -and $manifestBytes[1] -eq 0xBB -and $manifestBytes[2] -eq 0xBF) {
    throw "Bundle manifest should be UTF-8 without BOM."
}
if ([int]$manifest.schema -ne 1 -or -not [bool]$manifest.accepted_licenses -or [string]$manifest.model -ne "qwen2.5:3b") {
    throw "Bundle manifest contains unexpected metadata."
}
if ([int]$manifest.runtime.summary.files -lt 1 -or [int]$manifest.models.summary.files -lt 1) {
    throw "Bundle manifest summaries were not populated."
}
if (-not (Test-Path -LiteralPath (Join-Path $bundleOut "ollama\ollama.exe"))) {
    throw "Bundled runtime executable was not copied."
}
if (-not (Test-Path -LiteralPath (Join-Path $bundleOut "ollama-models\manifests\registry.ollama.ai\library\qwen2.5\3b"))) {
    throw "Bundled model manifest was not copied."
}
Write-Host "[ok] bundle manifest and copied files verified"

Assert-FailsWith -Label "sibling output root refused" -Arguments @(
    "-AcceptLicenses",
    "-SkipRuntime",
    "-SkipModels",
    "-OutputRoot", "..\mavris-bundle-evil"
) -Expected "must stay under"

$overlapRoot = Join-Path $workspacePath "overlap"
$overlapRuntime = Join-Path $overlapRoot "ollama"
New-Item -ItemType Directory -Path $overlapRuntime -Force | Out-Null
Set-Content -LiteralPath (Join-Path $overlapRuntime "ollama.exe") -Value "fake runtime" -Encoding ASCII

Assert-FailsWith -Label "runtime output cannot overwrite source" -Arguments @(
    "-AcceptLicenses",
    "-OllamaRuntimeDir", $overlapRuntime,
    "-SkipModels",
    "-OutputRoot", $overlapRoot
) -Expected "same as or inside the source"

$overlapModelsRoot = Join-Path $workspacePath "overlap-models"
$overlapModels = Join-Path $overlapModelsRoot "ollama-models"
$overlapModelManifest = Join-Path $overlapModels "manifests\registry.ollama.ai\library\qwen2.5\3b"
New-Item -ItemType Directory -Path (Split-Path -Parent $overlapModelManifest) -Force | Out-Null
Set-Content -LiteralPath $overlapModelManifest -Value "{}" -Encoding ASCII

Assert-FailsWith -Label "models output cannot overwrite source" -Arguments @(
    "-AcceptLicenses",
    "-SkipRuntime",
    "-OllamaModelsDir", $overlapModels,
    "-OutputRoot", $overlapModelsRoot
) -Expected "same as or inside the source"

Write-Host ""
Write-Host "Ollama bundle smoke passed." -ForegroundColor Green
