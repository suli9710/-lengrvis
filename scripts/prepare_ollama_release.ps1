param(
    [string]$OllamaRuntimeDir = "",
    [string]$OllamaModelsDir = "",
    [string]$OllamaExe = "",
    [string]$OutputRoot = ".lengrvis_data\ollama-release",
    [string]$Model = "qwen2.5:3b",
    [string]$PullDestination = "",
    [string]$PullHost = "127.0.0.1:11435",
    [switch]$PullModel,
    [switch]$AcceptLicenses,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path (Join-Path $scriptDir "..")).Path
}

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $script:repoRoot $Path
}

function Assert-ChildPath {
    param(
        [string]$Root,
        [string]$Candidate,
        [string]$Label
    )
    $rootPath = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\', '/')
    $candidatePath = if (Test-Path -LiteralPath $Candidate) {
        (Resolve-Path -LiteralPath $Candidate).Path
    } else {
        [System.IO.Path]::GetFullPath($Candidate)
    }
    $isChild = $candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith("$rootPath\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith("$rootPath/", [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $isChild) {
        throw "$Label must stay under $rootPath. Got: $candidatePath"
    }
}

function Invoke-Step {
    param(
        [string]$Label,
        [string]$ScriptPath,
        [string[]]$Arguments
    )
    Write-Step $Label
    & powershell -NoProfile -ExecutionPolicy Bypass -File $ScriptPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

function New-SmokeExecutable {
    param([string]$Path)
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [byte[]]$bytes = [byte[]]::new(4096)
    $peOffset = 0x80
    $bytes[0] = 0x4d
    $bytes[1] = 0x5a
    [System.BitConverter]::GetBytes([uint32]$peOffset).CopyTo($bytes, 0x3c)
    [System.BitConverter]::GetBytes([uint32]0x00004550).CopyTo($bytes, $peOffset)
    [System.BitConverter]::GetBytes([uint16]0x8664).CopyTo($bytes, $peOffset + 4)
    [System.BitConverter]::GetBytes([uint16]3).CopyTo($bytes, $peOffset + 6)
    [System.BitConverter]::GetBytes([uint16]0x00f0).CopyTo($bytes, $peOffset + 0x14)
    [System.BitConverter]::GetBytes([uint16]0x020b).CopyTo($bytes, $peOffset + 0x18)
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function New-SmokeSelfExtractingExecutable {
    param([string]$Path)
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [byte[]]$bytes = [byte[]]::new(131072)
    $peOffset = 0x80
    $bytes[0] = 0x4d
    $bytes[1] = 0x5a
    [System.BitConverter]::GetBytes([uint32]$peOffset).CopyTo($bytes, 0x3c)
    $bytes[$peOffset] = 0x50
    $bytes[$peOffset + 1] = 0x45
    [System.BitConverter]::GetBytes([uint16]0x8664).CopyTo($bytes, $peOffset + 4)
    [System.BitConverter]::GetBytes([uint16]3).CopyTo($bytes, $peOffset + 6)
    [System.BitConverter]::GetBytes([uint16]0x00f0).CopyTo($bytes, $peOffset + 0x14)
    [System.BitConverter]::GetBytes([uint16]0x020b).CopyTo($bytes, $peOffset + 0x18)
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

$repoRoot = Resolve-RepoRoot
$outputRootPath = Resolve-ProjectPath $OutputRoot
$manifestPath = Join-Path $outputRootPath "ollama-bundle-manifest.json"
$runtimeOut = Join-Path $outputRootPath "ollama"
$modelsOut = Join-Path $outputRootPath "ollama-models"

Assert-ChildPath -Root $repoRoot -Candidate $outputRootPath -Label "OutputRoot"

if (-not $AcceptLicenses) {
    throw @"
Pass -AcceptLicenses only after confirming these redistribution terms:
  1. The Ollama runtime license permits shipping it with Lengrvis.
  2. The model '$Model' license permits redistribution with Lengrvis.
  3. Any required attribution or notice files are included in the release notes.
"@
}

$bundleArgs = @(
    "-OutputRoot", $OutputRoot,
    "-Model", $Model,
    "-AcceptLicenses"
)

if ($OllamaRuntimeDir) { $bundleArgs += @("-OllamaRuntimeDir", $OllamaRuntimeDir) }
if ($OllamaModelsDir) { $bundleArgs += @("-OllamaModelsDir", $OllamaModelsDir) }
if ($OllamaExe) { $bundleArgs += @("-OllamaExe", $OllamaExe) }
if ($PullModel) { $bundleArgs += "-PullModel" }
if ($PullDestination) { $bundleArgs += @("-PullDestination", $PullDestination) }
if ($PullHost) { $bundleArgs += @("-PullHost", $PullHost) }

Invoke-Step -Label "Preparing explicit Ollama runtime and model resources" -ScriptPath (Join-Path $PSScriptRoot "bundle_ollama.ps1") -Arguments $bundleArgs

if (-not (Test-Path -LiteralPath $runtimeOut)) {
    throw "Prepared Ollama runtime directory is missing: $runtimeOut"
}
if (-not (Test-Path -LiteralPath $modelsOut)) {
    throw "Prepared Ollama models directory is missing: $modelsOut"
}
if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Prepared Ollama manifest is missing: $manifestPath"
}

if (-not $SkipVerify) {
    $verifyWorkspace = Join-Path $repoRoot ".tmp\ollama-release-verify"
    $distDir = Join-Path $verifyWorkspace "dist"
    $portableDir = Join-Path $distDir "Lengrvis-win-portable"
    $resourcesDir = Join-Path $portableDir "resources"
    $backendDir = Join-Path $resourcesDir "backend"
    $appDir = Join-Path $resourcesDir "app"
    $appDistDir = Join-Path $appDir "dist"
    $zipPath = Join-Path $distDir "Lengrvis-win-portable.zip"
    $desktopPackage = Get-Content -LiteralPath (Join-Path $repoRoot "desktop\package.json") -Raw | ConvertFrom-Json
    $selfExtracting = Join-Path $distDir "Lengrvis-$($desktopPackage.version)-x64-self-extracting.exe"

    if (Test-Path -LiteralPath $verifyWorkspace) {
        Remove-Item -LiteralPath $verifyWorkspace -Recurse -Force
    }
    New-Item -ItemType Directory -Path $backendDir,$appDistDir,$distDir -Force | Out-Null
    New-SmokeExecutable (Join-Path $distDir "backend.exe")
    New-SmokeExecutable (Join-Path $portableDir "Lengrvis.exe")
    New-SmokeExecutable (Join-Path $backendDir "backend.exe")
    $capabilityManifestJson = [ordered]@{
        schema = "lengrvis-backend-capabilities/v1"
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        python = "smoke"
        platform = "win32"
        capabilities = [ordered]@{
            docling = $false
            unstructured = $false
            paddleocr = $false
            pytesseract = $false
            playwright = $false
            pywhispercpp = $false
        }
    } | ConvertTo-Json -Depth 4
    Set-Content -LiteralPath (Join-Path $distDir "backend-capabilities.json") -Value $capabilityManifestJson -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $backendDir "backend-capabilities.json") -Value $capabilityManifestJson -Encoding ASCII
    New-SmokeSelfExtractingExecutable $selfExtracting
    Set-Content -LiteralPath (Join-Path $appDir "package.json") -Value '{"name":"lengrvis-ollama-release-verify"}' -Encoding ASCII
    Copy-Item -LiteralPath $runtimeOut -Destination (Join-Path $resourcesDir "ollama") -Recurse -Force
    Copy-Item -LiteralPath $modelsOut -Destination (Join-Path $resourcesDir "ollama-models") -Recurse -Force
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $resourcesDir "ollama-bundle-manifest.json") -Force

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($portableDir, $zipPath)

    Invoke-Step -Label "Verifying prepared Ollama resources against packaging gate" -ScriptPath (Join-Path $PSScriptRoot "verify_packaging.ps1") -Arguments @(
        "-DistDir", $distDir,
        "-PortableDir", $portableDir,
        "-PortableZip", $zipPath,
        "-SelfExtractingExe", $selfExtracting,
        "-RequireBundledOllama"
    )
}

Write-Host ""
Write-Host "Ollama release resources are ready in $outputRootPath" -ForegroundColor Green
Write-Host "Next: run scripts\build_all.ps1 -BundledOllamaDir `"$runtimeOut`" -BundledOllamaModelsDir `"$modelsOut`" -BundledOllamaManifest `"$manifestPath`" -RequireBundledOllama"
