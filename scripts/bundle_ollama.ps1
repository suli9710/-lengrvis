param(
    [string]$OllamaRuntimeDir = "",
    [string]$OllamaModelsDir = "",
    [string]$OllamaExe = "",
    [string]$OutputRoot = ".marvis_data\ollama-release",
    [string]$Model = "qwen2.5:3b",
    [string]$RuntimeSource = "",
    [string]$ModelSource = "",
    [string]$PullDestination = "",
    [string]$PullHost = "127.0.0.1:11435",
    [switch]$UseInstalledOllama,
    [switch]$PullModel,
    [switch]$AcceptLicenses,
    [switch]$SkipRuntime,
    [switch]$SkipModels
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

function Assert-ChildPath {
    param(
        [string]$Root,
        [string]$Candidate,
        [string]$Label
    )
    $rootPath = (Resolve-Path -LiteralPath $Root).Path
    $candidatePath = if (Test-Path -LiteralPath $Candidate) {
        (Resolve-Path -LiteralPath $Candidate).Path
    } else {
        [System.IO.Path]::GetFullPath($Candidate)
    }
    $rootPath = $rootPath.TrimEnd('\', '/')
    $isChild = $candidatePath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith("$rootPath\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith("$rootPath/", [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $isChild) {
        throw "$Label must stay under $rootPath. Got: $candidatePath"
    }
}

function Resolve-CanonicalPath {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        return (Resolve-Path -LiteralPath $Path).Path.TrimEnd('\', '/')
    }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-SameOrNestedPath {
    param(
        [string]$Parent,
        [string]$Candidate
    )
    $parentPath = (Resolve-CanonicalPath -Path $Parent)
    $candidatePath = (Resolve-CanonicalPath -Path $Candidate)
    return $candidatePath.Equals($parentPath, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith("$parentPath\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidatePath.StartsWith("$parentPath/", [System.StringComparison]::OrdinalIgnoreCase)
}

function Copy-DirectoryClean {
    param(
        [string]$Source,
        [string]$Destination,
        [string]$Label
    )
    if (-not (Test-Path -LiteralPath $Source)) {
        throw "$Label source directory was not found: $Source"
    }
    if ($script:repoRoot) {
        Assert-ChildPath -Root $script:repoRoot -Candidate $Destination -Label "$Label destination"
    }
    if (Test-SameOrNestedPath -Parent $Source -Candidate $Destination) {
        throw "$Label destination must not be the same as or inside the source directory. Source: $Source Destination: $Destination"
    }
    if (Test-SameOrNestedPath -Parent $Destination -Candidate $Source) {
        throw "$Label source must not be inside the destination directory. Source: $Source Destination: $Destination"
    }
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Join-PathSegments {
    param(
        [string]$Root,
        [string[]]$Segments
    )
    $current = $Root
    foreach ($segment in $Segments) {
        if ($segment) {
            $current = Join-Path $current $segment
        }
    }
    return $current
}

function Get-OllamaExecutableName {
    if ($IsWindows -or $env:OS -eq "Windows_NT") {
        return "ollama.exe"
    }
    return "ollama"
}

function Resolve-OllamaExecutable {
    param([string]$ExplicitPath)
    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath $ExplicitPath)) {
            throw "Ollama executable was not found: $ExplicitPath"
        }
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }

    $command = Get-Command "ollama" -ErrorAction SilentlyContinue
    if ($command -and $command.Source -and (Test-Path -LiteralPath $command.Source)) {
        return (Resolve-Path -LiteralPath $command.Source).Path
    }

    $exeName = Get-OllamaExecutableName
    $candidates = @()
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Ollama\$exeName"
    }
    if ($env:ProgramFiles) {
        $candidates += Join-Path $env:ProgramFiles "Ollama\$exeName"
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += Join-Path ${env:ProgramFiles(x86)} "Ollama\$exeName"
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return ""
}

function Find-OllamaExecutableInRuntimeDir {
    param([string]$RuntimeDir)
    $exeName = Get-OllamaExecutableName
    $candidates = @(
        (Join-Path $RuntimeDir $exeName),
        (Join-Path $RuntimeDir "bin\$exeName"),
        (Join-Path $RuntimeDir "Ollama\$exeName")
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return ""
}

function Resolve-OllamaRuntimeDir {
    param(
        [string]$ExplicitDir,
        [string]$ExecutablePath
    )
    if ($ExplicitDir) {
        $resolvedDir = (Resolve-Path -LiteralPath $ExplicitDir).Path
        return [ordered]@{
            path = $resolvedDir
            executable = Find-OllamaExecutableInRuntimeDir -RuntimeDir $resolvedDir
            source = "explicit"
        }
    }

    $resolvedExe = Resolve-OllamaExecutable -ExplicitPath $ExecutablePath
    if (-not $resolvedExe) {
        throw "Ollama runtime was not found. Pass -OllamaRuntimeDir, pass -OllamaExe, install Ollama, or use -SkipRuntime."
    }

    $runtimeDir = Split-Path -Parent $resolvedExe
    if ((Split-Path -Leaf $runtimeDir) -ieq "bin") {
        $parent = Split-Path -Parent $runtimeDir
        if ($parent -and (Test-Path -LiteralPath $parent)) {
            $runtimeDir = $parent
        }
    }
    return [ordered]@{
        path = (Resolve-Path -LiteralPath $runtimeDir).Path
        executable = $resolvedExe
        source = "installed-ollama"
    }
}

function Resolve-OllamaModelsDir {
    param([string]$ExplicitDir)
    if ($ExplicitDir) {
        return [ordered]@{
            path = (Resolve-Path -LiteralPath $ExplicitDir).Path
            source = "explicit"
        }
    }

    $candidates = @()
    if ($env:OLLAMA_MODELS) {
        $candidates += [ordered]@{ path = $env:OLLAMA_MODELS; source = "env:OLLAMA_MODELS" }
    }
    if ($HOME) {
        $candidates += [ordered]@{ path = (Join-Path $HOME ".ollama\models"); source = "ollama-default-model-store" }
        $candidates += [ordered]@{ path = (Join-Path $HOME ".ollama"); source = "ollama-home" }
    }

    foreach ($candidate in $candidates) {
        $candidatePath = [string]$candidate.path
        if (-not $candidatePath -or -not (Test-Path -LiteralPath $candidatePath)) {
            continue
        }
        if (Test-Path -LiteralPath (Join-Path $candidatePath "manifests")) {
            return [ordered]@{
                path = (Resolve-Path -LiteralPath $candidatePath).Path
                source = [string]$candidate.source
            }
        }
        $nestedModels = Join-Path $candidatePath "models"
        if (Test-Path -LiteralPath (Join-Path $nestedModels "manifests")) {
            return [ordered]@{
                path = (Resolve-Path -LiteralPath $nestedModels).Path
                source = [string]$candidate.source
            }
        }
    }

    throw "Ollama models were not found. Pass -OllamaModelsDir, run with -PullModel, set OLLAMA_MODELS, or use -SkipModels."
}

function Get-ModelManifestPath {
    param(
        [string]$ModelsDir,
        [string]$ModelRef
    )
    $modelName = $ModelRef
    $tag = "latest"
    $colonIndex = $ModelRef.LastIndexOf(":")
    if ($colonIndex -gt 0) {
        $modelName = $ModelRef.Substring(0, $colonIndex)
        $tag = $ModelRef.Substring($colonIndex + 1)
    }

    $segments = @($modelName -split "/" | Where-Object { $_ })
    if (@($segments).Count -eq 0) {
        throw "Model name is empty."
    }
    if (@($segments).Count -eq 1) {
        $manifestSegments = @("registry.ollama.ai", "library", $segments[0], $tag)
    } elseif (@($segments).Count -eq 2) {
        $manifestSegments = @("registry.ollama.ai", $segments[0], $segments[1], $tag)
    } else {
        $manifestSegments = @($segments + $tag)
    }

    return Join-PathSegments -Root (Join-Path $ModelsDir "manifests") -Segments $manifestSegments
}

function Get-BlobPathForDigest {
    param(
        [string]$ModelsDir,
        [string]$Digest
    )
    if (-not $Digest) {
        return ""
    }
    $fileName = $Digest.Replace(":", "-")
    return Join-PathSegments -Root $ModelsDir -Segments @("blobs", $fileName)
}

function Assert-ModelManifestBlobs {
    param(
        [string]$ModelsDir,
        [string]$ManifestPath
    )
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $digests = @()
    if ($manifest.config -and $manifest.config.digest) {
        $digests += [string]$manifest.config.digest
    }
    if ($manifest.layers) {
        foreach ($layer in @($manifest.layers)) {
            if ($layer.digest) {
                $digests += [string]$layer.digest
            }
        }
    }
    foreach ($digest in $digests) {
        $blobPath = Get-BlobPathForDigest -ModelsDir $ModelsDir -Digest $digest
        if ($blobPath -and -not (Test-Path -LiteralPath $blobPath)) {
            throw "Ollama model manifest references missing blob $digest at $blobPath"
        }
    }
}

function Get-RelativePath {
    param(
        [string]$Root,
        [string]$Path
    )
    if (-not $Root -or -not $Path) {
        return ""
    }
    $rootPath = (Resolve-Path -LiteralPath $Root).Path.TrimEnd('\', '/')
    $targetPath = (Resolve-Path -LiteralPath $Path).Path
    if ($targetPath.Equals($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return ""
    }
    return $targetPath.Substring($rootPath.Length).TrimStart('\', '/')
}

function Get-OllamaVersion {
    param([string]$ExecutablePath)
    if (-not $ExecutablePath -or -not (Test-Path -LiteralPath $ExecutablePath)) {
        return ""
    }
    try {
        $output = & $ExecutablePath "--version" 2>$null
        return (($output | Select-Object -First 1) -as [string])
    } catch {
        return ""
    }
}

function Get-OllamaHostUri {
    param([string]$HostValue)
    if ($HostValue.StartsWith("http://", [System.StringComparison]::OrdinalIgnoreCase) -or
        $HostValue.StartsWith("https://", [System.StringComparison]::OrdinalIgnoreCase)) {
        return $HostValue
    }
    return "http://$HostValue"
}

function Start-PrivateOllamaServer {
    param(
        [string]$ExecutablePath,
        [string]$ModelsDir,
        [string]$HostValue
    )
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $ExecutablePath
    $psi.Arguments = "serve"
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.Environment["OLLAMA_MODELS"] = $ModelsDir
    $psi.Environment["OLLAMA_HOST"] = $HostValue
    return [System.Diagnostics.Process]::Start($psi)
}

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Value
    )
    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

function Wait-OllamaServer {
    param(
        [string]$HostValue,
        [int]$TimeoutSeconds = 30
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $uri = "$(Get-OllamaHostUri -HostValue $HostValue)/api/tags"
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 2 | Out-Null
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "Timed out waiting for Ollama at $uri"
}

function Invoke-OllamaPull {
    param(
        [string]$ExecutablePath,
        [string]$ModelsDir,
        [string]$ModelRef,
        [string]$HostValue
    )
    if (-not $ExecutablePath) {
        throw "Ollama executable is required for -PullModel. Pass -OllamaExe or install Ollama."
    }
    New-Item -ItemType Directory -Path $ModelsDir -Force | Out-Null
    Write-Step "Starting private Ollama service for model pull"
    $process = Start-PrivateOllamaServer -ExecutablePath $ExecutablePath -ModelsDir $ModelsDir -HostValue $HostValue
    try {
        Wait-OllamaServer -HostValue $HostValue
        Write-Step "Pulling model $ModelRef into staging model store"
        $oldHost = $env:OLLAMA_HOST
        $oldModels = $env:OLLAMA_MODELS
        try {
            $env:OLLAMA_HOST = $HostValue
            $env:OLLAMA_MODELS = $ModelsDir
            & $ExecutablePath "pull" $ModelRef
            if ($LASTEXITCODE -ne 0) {
                throw "ollama pull exited with code $LASTEXITCODE"
            }
        } finally {
            $env:OLLAMA_HOST = $oldHost
            $env:OLLAMA_MODELS = $oldModels
        }
    } finally {
        if ($process -and -not $process.HasExited) {
            $process.Kill()
            $process.WaitForExit()
        }
    }
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
    $files = Get-ChildItem -LiteralPath $Path -File -Recurse -Force | Sort-Object FullName
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $files) {
            $relative = $file.FullName.Substring((Resolve-Path -LiteralPath $Path).Path.Length).TrimStart('\', '/')
            $nameBytes = [System.Text.Encoding]::UTF8.GetBytes($relative.ToLowerInvariant())
            $hash.TransformBlock($nameBytes, 0, $nameBytes.Length, $null, 0) | Out-Null
            $content = [System.IO.File]::ReadAllBytes($file.FullName)
            $hash.TransformBlock($content, 0, $content.Length, $null, 0) | Out-Null
        }
        $hash.TransformFinalBlock([byte[]]::new(0), 0, 0) | Out-Null
        $digest = -join ($hash.Hash | ForEach-Object { $_.ToString("x2") })
    } finally {
        $hash.Dispose()
    }
    return [ordered]@{
        present = $true
        files = @($files).Count
        bytes = [int64](($files | Measure-Object -Property Length -Sum).Sum)
        sha256 = $digest
    }
}

function Compare-Summary {
    param(
        [object]$Expected,
        [object]$Actual,
        [string]$Label
    )
    if ([bool]$Expected.present -ne [bool]$Actual.present) {
        throw "$Label manifest present flag does not match current files."
    }
    if ([int64]$Expected.files -ne [int64]$Actual.files) {
        throw "$Label manifest file count does not match current files."
    }
    if ([int64]$Expected.bytes -ne [int64]$Actual.bytes) {
        throw "$Label manifest byte count does not match current files."
    }
    if ([string]$Expected.sha256 -ne [string]$Actual.sha256) {
        throw "$Label manifest sha256 does not match current files."
    }
}

function Test-OllamaBundleManifest {
    param(
        [string]$ManifestPath,
        [string]$RuntimeDir,
        [string]$ModelsDir
    )
    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        throw "Ollama bundle manifest was not found: $ManifestPath"
    }
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ([int]$manifest.schema -ne 1) {
        throw "Unsupported Ollama bundle manifest schema: $($manifest.schema)"
    }
    if (-not [bool]$manifest.accepted_licenses) {
        throw "Ollama bundle manifest must confirm accepted_licenses=true."
    }
    Compare-Summary -Expected $manifest.runtime.summary -Actual (Get-DirectorySummary -Path $RuntimeDir) -Label "Runtime"
    Compare-Summary -Expected $manifest.models.summary -Actual (Get-DirectorySummary -Path $ModelsDir) -Label "Models"
    return $manifest
}

function Assert-OllamaRuntime {
    param([string]$Path)
    $exePath = Find-OllamaExecutableInRuntimeDir -RuntimeDir $Path
    if ($exePath) {
        return
    }
    $exeName = Get-OllamaExecutableName
    throw "Ollama runtime directory must contain $exeName at root, bin\, or Ollama\: $Path"
}

function Assert-OllamaModels {
    param([string]$Path)
    $manifests = Join-Path $Path "manifests"
    if (-not (Test-Path -LiteralPath $manifests)) {
        throw "Ollama models directory must contain a manifests directory: $Path"
    }
    $modelManifestPath = Get-ModelManifestPath -ModelsDir $Path -ModelRef $Model
    if (-not (Test-Path -LiteralPath $modelManifestPath)) {
        throw "Ollama models directory does not contain manifest for $Model at $modelManifestPath"
    }
    Assert-ModelManifestBlobs -ModelsDir $Path -ManifestPath $modelManifestPath
}

function New-BundleManifest {
    param(
        [string]$RuntimeDir,
        [string]$ModelsDir,
        [string]$ManifestPath,
        [string]$RuntimeExecutable = ""
    )
    $modelManifestPath = if ($ModelsDir -and (Test-Path -LiteralPath $ModelsDir)) {
        $candidate = Get-ModelManifestPath -ModelsDir $ModelsDir -ModelRef $Model
        if (Test-Path -LiteralPath $candidate) { $candidate } else { "" }
    } else {
        ""
    }
    $payload = [ordered]@{
        schema = 1
        created_at = (Get-Date).ToUniversalTime().ToString("o")
        model = $Model
        accepted_licenses = [bool]$AcceptLicenses
        license_confirmation = "Runtime and model redistribution licenses were explicitly accepted by the packager."
        tool = [ordered]@{
            script = "scripts/bundle_ollama.ps1"
            pull_model = [bool]$PullModel
            pull_host = if ($PullModel) { $PullHost } else { "" }
        }
        runtime = [ordered]@{
            source = $RuntimeSource
            path = if ($RuntimeDir) { Get-RelativePath -Root $repoRoot -Path $RuntimeDir } else { "" }
            executable = if ($RuntimeExecutable) { Get-RelativePath -Root $RuntimeDir -Path $RuntimeExecutable } else { "" }
            version = Get-OllamaVersion -ExecutablePath $RuntimeExecutable
            summary = Get-DirectorySummary -Path $RuntimeDir
        }
        models = [ordered]@{
            source = $ModelSource
            path = if ($ModelsDir) { Get-RelativePath -Root $repoRoot -Path $ModelsDir } else { "" }
            model_manifest = if ($modelManifestPath) { Get-RelativePath -Root $ModelsDir -Path $modelManifestPath } else { "" }
            summary = Get-DirectorySummary -Path $ModelsDir
        }
    }
    Write-Utf8NoBom -Path $ManifestPath -Value ($payload | ConvertTo-Json -Depth 8)
}

$repoRoot = Resolve-RepoRoot
$outputRootPath = if ([System.IO.Path]::IsPathRooted($OutputRoot)) { $OutputRoot } else { Join-Path $repoRoot $OutputRoot }
$runtimeOut = Join-Path $outputRootPath "ollama"
$modelsOut = Join-Path $outputRootPath "ollama-models"
$manifestPath = Join-Path $outputRootPath "ollama-bundle-manifest.json"
$resolvedRuntimeExecutable = ""

Assert-ChildPath -Root $repoRoot -Candidate $outputRootPath -Label "OutputRoot"
New-Item -ItemType Directory -Path $outputRootPath -Force | Out-Null

if (-not $AcceptLicenses) {
    throw "Pass -AcceptLicenses only after confirming the Ollama runtime and model '$Model' licenses allow redistribution with Mavris."
}

if (-not $SkipRuntime) {
    $runtimeResolution = Resolve-OllamaRuntimeDir -ExplicitDir $OllamaRuntimeDir -ExecutablePath $OllamaExe
    $OllamaRuntimeDir = [string]$runtimeResolution.path
    $resolvedRuntimeExecutable = [string]$runtimeResolution.executable
    if (-not $RuntimeSource) {
        $RuntimeSource = [string]$runtimeResolution.source
    }
    Write-Step "Copying Ollama runtime"
    Assert-OllamaRuntime -Path $OllamaRuntimeDir
    Copy-DirectoryClean -Source $OllamaRuntimeDir -Destination $runtimeOut -Label "Ollama runtime"
}

if (-not $SkipModels) {
    if ($PullModel) {
        if (-not $PullDestination) {
            $PullDestination = Join-PathSegments -Root $repoRoot -Segments @(".marvis_data", "ollama-bundle-staging", "models")
        } elseif (-not [System.IO.Path]::IsPathRooted($PullDestination)) {
            $PullDestination = Join-Path $repoRoot $PullDestination
        }
        Assert-ChildPath -Root $repoRoot -Candidate $PullDestination -Label "PullDestination"
        $pullExe = if ($resolvedRuntimeExecutable) { $resolvedRuntimeExecutable } else { Resolve-OllamaExecutable -ExplicitPath $OllamaExe }
        Invoke-OllamaPull -ExecutablePath $pullExe -ModelsDir $PullDestination -ModelRef $Model -HostValue $PullHost
        $OllamaModelsDir = $PullDestination
        if (-not $ModelSource) {
            $ModelSource = "ollama-pull-staging"
        }
    } else {
        $modelsResolution = Resolve-OllamaModelsDir -ExplicitDir $OllamaModelsDir
        $OllamaModelsDir = [string]$modelsResolution.path
        if (-not $ModelSource) {
            $ModelSource = [string]$modelsResolution.source
        }
    }
    Write-Step "Copying Ollama models"
    Assert-OllamaModels -Path $OllamaModelsDir
    Copy-DirectoryClean -Source $OllamaModelsDir -Destination $modelsOut -Label "Ollama models"
}

Write-Step "Writing bundle manifest"
New-BundleManifest -RuntimeDir $(if (Test-Path -LiteralPath $runtimeOut) { $runtimeOut } else { "" }) -ModelsDir $(if (Test-Path -LiteralPath $modelsOut) { $modelsOut } else { "" }) -ManifestPath $manifestPath -RuntimeExecutable $(if ($resolvedRuntimeExecutable -and (Test-Path -LiteralPath $resolvedRuntimeExecutable)) { Join-Path $runtimeOut (Get-RelativePath -Root $OllamaRuntimeDir -Path $resolvedRuntimeExecutable) } else { "" })
Test-OllamaBundleManifest -ManifestPath $manifestPath -RuntimeDir $(if (Test-Path -LiteralPath $runtimeOut) { $runtimeOut } else { "" }) -ModelsDir $(if (Test-Path -LiteralPath $modelsOut) { $modelsOut } else { "" }) | Out-Null
Write-Host "Ollama bundle manifest written to $manifestPath"
