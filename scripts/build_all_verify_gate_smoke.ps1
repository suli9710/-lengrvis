param(
    [string]$Workspace = ".tmp\build-all-verify-gate-smoke"
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

function New-SmokeExecutable([string]$Path) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [byte[]]$bytes = 0x4d,0x5a,0x90,0x00
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function New-SmokeSelfExtractingExecutable([string]$Path) {
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

function Get-DirectorySummary([string]$Path) {
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

function New-SmokePackage {
    param(
        [string]$PackageRoot,
        [switch]$IncludeOllama
    )
    $dist = Join-Path $PackageRoot "dist"
    $portable = Join-Path $dist "Mavris-win-portable"
    $resources = Join-Path $portable "resources"
    New-SmokeExecutable (Join-Path $dist "backend.exe")
    New-SmokeExecutable (Join-Path $portable "Mavris.exe")
    New-SmokeExecutable (Join-Path $resources "backend\backend.exe")
    New-Item -ItemType Directory -Path (Join-Path $resources "app\dist") -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $resources "app\package.json") -Value '{"name":"mavris-smoke"}' -Encoding ASCII
    New-SmokeSelfExtractingExecutable (Join-Path $dist "Mavris-0.1.0-x64-self-extracting.exe")

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
            runtime = [ordered]@{ summary = Get-DirectorySummary -Path $ollamaDir }
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
}

function Invoke-BuildVerify {
    param(
        [string]$ScriptName,
        [string]$PackageRoot,
        [switch]$RequireBundledOllama
    )
    $dist = Join-Path $PackageRoot "dist"
    $portable = Join-Path $dist "Mavris-win-portable"
    $zip = Join-Path $dist "Mavris-win-portable.zip"
    $selfExtracting = Join-Path $dist "Mavris-0.1.0-x64-self-extracting.exe"
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "$PSScriptRoot\$ScriptName",
        "-VerifyOnly",
        "-DistDir",
        $dist,
        "-PortableDir",
        $portable,
        "-PortableZip",
        $zip,
        "-SelfExtractingExe",
        $selfExtracting
    )
    if ($RequireBundledOllama) {
        $args += "-RequireBundledOllama"
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell @args 2>&1
        $script:LastBuildAllVerifyExitCode = $LASTEXITCODE
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

$withOllama = Join-Path $workspacePath "with-ollama"
$withoutOllama = Join-Path $workspacePath "without-ollama"
New-SmokePackage -PackageRoot $withOllama -IncludeOllama
New-SmokePackage -PackageRoot $withoutOllama

$output = Invoke-BuildVerify -ScriptName "build_all.ps1" -PackageRoot $withOllama -RequireBundledOllama
if ($script:LastBuildAllVerifyExitCode -ne 0) {
    throw "Expected build_all -VerifyOnly -RequireBundledOllama to pass with bundled resources:`n$output"
}
Write-Host "[ok] build_all verify gate passes with bundled Ollama"

$output = Invoke-BuildVerify -ScriptName "build_all.ps1" -PackageRoot $withoutOllama -RequireBundledOllama
if ($script:LastBuildAllVerifyExitCode -eq 0) {
    throw "Expected build_all -VerifyOnly -RequireBundledOllama to fail without bundled resources."
}
$text = $output | Out-String
if ($text -notmatch "portable Ollama runtime missing") {
    throw "Unexpected build_all verify failure output:`n$text"
}
Write-Host "[ok] build_all verify gate fails without bundled Ollama when required"

$output = Invoke-BuildVerify -ScriptName "build_all.ps1" -PackageRoot $withoutOllama
if ($script:LastBuildAllVerifyExitCode -ne 0) {
    throw "Expected build_all -VerifyOnly to pass without bundled resources when not required:`n$output"
}
Write-Host "[ok] build_all verify gate remains compatible without bundled requirement"

$output = Invoke-BuildVerify -ScriptName "build.ps1" -PackageRoot $withOllama -RequireBundledOllama
if ($script:LastBuildAllVerifyExitCode -ne 0) {
    throw "Expected build.ps1 wrapper to pass through -VerifyOnly -RequireBundledOllama:`n$output"
}
Write-Host "[ok] build.ps1 wrapper passes verify gate arguments"

Write-Host ""
Write-Host "build_all verification gate smoke passed." -ForegroundColor Green
