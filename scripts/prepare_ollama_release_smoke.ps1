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

function Get-DirectorySummary {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return [ordered]@{
            present = $false
            files = 0
            bytes = 0
            sha256 = ""
        }
    }
    $rootPath = (Resolve-Path -LiteralPath $Path).Path
    $files = Get-ChildItem -LiteralPath $Path -File -Recurse -Force | Sort-Object FullName
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($rootPath.Length).TrimStart('\', '/')
            $nameBytes = [System.Text.Encoding]::UTF8.GetBytes($relative.ToLowerInvariant())
            $hash.TransformBlock($nameBytes, 0, $nameBytes.Length, $null, 0) | Out-Null
            $content = [System.IO.File]::ReadAllBytes($file.FullName)
            $hash.TransformBlock($content, 0, $content.Length, $null, 0) | Out-Null
        }
        $hash.TransformFinalBlock([byte[]]::new(0), 0, 0) | Out-Null
        $digest = -join ($hash.Hash | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $hash.Dispose()
    }
    return [ordered]@{
        present = $true
        files = @($files).Count
        bytes = [int64](($files | Measure-Object -Property Length -Sum).Sum)
        sha256 = $digest
    }
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
$runtimeSummary = Get-DirectorySummary -Path $runtime
$modelsSummary = Get-DirectorySummary -Path $models

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
    "-ExpectedRuntimeSha256", $runtimeSummary.sha256,
    "-ExpectedModelsSha256", "sha256:$($modelsSummary.sha256)",
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
$manifest = Get-Content -LiteralPath (Join-Path $outputRoot "ollama-bundle-manifest.json") -Raw | ConvertFrom-Json
if ([string]$manifest.provenance.runtime.expected_sha256 -ne [string]$runtimeSummary.sha256) {
    throw "Prepared bundle manifest did not record expected runtime sha256."
}
if ([string]$manifest.provenance.models.expected_sha256 -ne [string]$modelsSummary.sha256) {
    throw "Prepared bundle manifest did not record expected models sha256."
}

Assert-FailsWith -Label "prepare release refuses expected models sha256 mismatch" -Arguments @(
    "-AcceptLicenses",
    "-OllamaRuntimeDir", $runtime,
    "-OllamaModelsDir", $models,
    "-OutputRoot", (Join-Path $workspacePath "vendor-bad-hash"),
    "-ExpectedRuntimeSha256", $runtimeSummary.sha256,
    "-ExpectedModelsSha256", "2222222222222222222222222222222222222222222222222222222222222222",
    "-Model", "qwen2.5:3b",
    "-SkipVerify"
) -Expected "Models sha256 mismatch"

Write-Host ""
Write-Host "Prepare Ollama release smoke passed." -ForegroundColor Green
