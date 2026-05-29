param(
    [string]$Workspace = ".tmp\prepare-ollama-release-smoke"
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

function Invoke-PrepareRelease {
    param([string[]]$Arguments)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\prepare_ollama_release.ps1" @Arguments 2>&1
        $script:LastPrepareReleaseExitCode = $LASTEXITCODE
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
    $output = Invoke-PrepareRelease -Arguments $Arguments
    if ($script:LastPrepareReleaseExitCode -ne 0) {
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
    $output = Invoke-PrepareRelease -Arguments $Arguments
    if ($script:LastPrepareReleaseExitCode -eq 0) {
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

$runtime = Join-Path $workspacePath "runtime"
$models = Join-Path $workspacePath "models"
$outputRoot = Join-Path $workspacePath "vendor"
$modelManifest = Join-Path $models "manifests\registry.ollama.ai\library\qwen2.5\3b"

New-Item -ItemType Directory -Path $runtime,(Split-Path -Parent $modelManifest) -Force | Out-Null
Set-Content -LiteralPath (Join-Path $runtime "ollama.exe") -Value "fake runtime" -Encoding ASCII
Set-Content -LiteralPath $modelManifest -Value "{}" -Encoding ASCII

Assert-FailsWith -Label "license confirmation required" -Arguments @(
    "-OllamaRuntimeDir", $runtime,
    "-OllamaModelsDir", $models,
    "-OutputRoot", $outputRoot
) -Expected "Pass -AcceptLicenses"

Assert-Passes -Label "prepare release resources and verify packaging gate" -Arguments @(
    "-AcceptLicenses",
    "-OllamaRuntimeDir", $runtime,
    "-OllamaModelsDir", $models,
    "-OutputRoot", $outputRoot,
    "-Model", "qwen2.5:3b"
)

if (-not (Test-Path -LiteralPath (Join-Path $outputRoot "ollama\ollama.exe"))) {
    throw "Prepared runtime missing."
}
if (-not (Test-Path -LiteralPath (Join-Path $outputRoot "ollama-models\manifests\registry.ollama.ai\library\qwen2.5\3b"))) {
    throw "Prepared model manifest missing."
}
if (-not (Test-Path -LiteralPath (Join-Path $outputRoot "ollama-bundle-manifest.json"))) {
    throw "Prepared bundle manifest missing."
}

Write-Host ""
Write-Host "Prepare Ollama release smoke passed." -ForegroundColor Green
