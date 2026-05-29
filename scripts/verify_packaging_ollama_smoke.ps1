param(
    [string]$Workspace = ".tmp\verify-packaging-ollama-smoke"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Resolve-SmokePath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Get-DirectorySummary {
    param([string]$Path)
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

function New-SmokeExecutable([string]$Path) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [byte[]]$bytes = 0x4d,0x5a,0x90,0x00
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function New-SmokePackage {
    param(
        [string]$RootPath,
        [switch]$IncludeOllama
    )
    $dist = Join-Path $RootPath "dist"
    $portable = Join-Path $dist "Mavris-win-portable"
    $resources = Join-Path $portable "resources"
    New-SmokeExecutable (Join-Path $dist "backend.exe")
    New-SmokeExecutable (Join-Path $portable "Mavris.exe")
    New-SmokeExecutable (Join-Path $resources "backend\backend.exe")
    New-Item -ItemType Directory -Path (Join-Path $resources "app\dist") -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $resources "app\package.json") -Value '{"name":"mavris-smoke"}' -Encoding ASCII
    New-SmokeExecutable (Join-Path $dist "Mavris-0.1.0-x64-self-extracting.exe")

    if ($IncludeOllama) {
        $ollamaDir = Join-Path $resources "ollama"
        $modelsDir = Join-Path $resources "ollama-models"
        $modelManifest = Join-Path $modelsDir "manifests\registry.ollama.ai\library\qwen2.5\3b"
        New-SmokeExecutable (Join-Path $ollamaDir "ollama.exe")
        New-Item -ItemType Directory -Path (Split-Path -Parent $modelManifest) -Force | Out-Null
        Set-Content -LiteralPath $modelManifest -Value '{}' -Encoding ASCII
        $manifest = [ordered]@{
            schema = 1
            model = "qwen2.5:3b"
            accepted_licenses = $true
            runtime = [ordered]@{
                summary = Get-DirectorySummary -Path $ollamaDir
            }
            models = [ordered]@{
                model_manifest = "manifests/registry.ollama.ai/library/qwen2.5/3b"
                summary = Get-DirectorySummary -Path $modelsDir
            }
        }
        $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $resources "ollama-bundle-manifest.json") -Encoding UTF8
    }

    $zipPath = Join-Path $dist "Mavris-win-portable.zip"
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($portable, $zipPath)
    return [ordered]@{
        dist = $dist
        portable = $portable
        zip = $zipPath
        selfExtracting = Join-Path $dist "Mavris-0.1.0-x64-self-extracting.exe"
    }
}

function Invoke-VerifyPackaging {
    param(
        [object]$Package,
        [switch]$RequireBundledOllama
    )
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "$PSScriptRoot\verify_packaging.ps1",
        "-DistDir",
        $Package.dist,
        "-PortableDir",
        $Package.portable,
        "-PortableZip",
        $Package.zip,
        "-SelfExtractingExe",
        $Package.selfExtracting
    )
    if ($RequireBundledOllama) {
        $args += "-RequireBundledOllama"
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell @args 2>&1
        $script:LastVerifyPackagingExitCode = $LASTEXITCODE
        return $output
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

$workspacePath = Resolve-SmokePath $Workspace
if (Test-Path -LiteralPath $workspacePath) {
    Remove-Item -LiteralPath $workspacePath -Recurse -Force
}
New-Item -ItemType Directory -Path $workspacePath -Force | Out-Null

$withOllama = New-SmokePackage -RootPath (Join-Path $workspacePath "with-ollama") -IncludeOllama
$withoutOllama = New-SmokePackage -RootPath (Join-Path $workspacePath "without-ollama")

$output = Invoke-VerifyPackaging -Package $withOllama -RequireBundledOllama
if ($script:LastVerifyPackagingExitCode -ne 0) {
    throw "Expected bundled Ollama package verification to pass:`n$output"
}
Write-Host "[ok] package with bundled Ollama passes"

$output = Invoke-VerifyPackaging -Package $withoutOllama -RequireBundledOllama
if ($script:LastVerifyPackagingExitCode -eq 0) {
    throw "Expected package without bundled Ollama to fail."
}
$text = $output | Out-String
if ($text -notmatch "portable Ollama runtime missing") {
    throw "Unexpected missing Ollama failure output:`n$text"
}
Write-Host "[ok] package without bundled Ollama fails when required"

$output = Invoke-VerifyPackaging -Package $withoutOllama
if ($script:LastVerifyPackagingExitCode -ne 0) {
    throw "Expected package without bundled Ollama to pass without -RequireBundledOllama:`n$output"
}
Write-Host "[ok] package without bundled Ollama remains valid when not required"

Write-Host ""
Write-Host "Bundled Ollama packaging smoke passed." -ForegroundColor Green
