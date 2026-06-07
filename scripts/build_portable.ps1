param(
    [string]$OutputDir = "dist\Lengrvis-win-portable",
    [string]$BackendExe = "dist\backend.exe",
    [string]$BundledOllamaDir = "",
    [string]$BundledOllamaModelsDir = "",
    [string]$BundledOllamaManifest = "",
    [switch]$PathSafetyCheckOnly
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Resolve-CanonicalPath {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path) {
        return (Resolve-Path -LiteralPath $Path).Path.TrimEnd('\', '/')
    }
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Test-SameOrNestedPath {
    param(
        [string]$Parent,
        [string]$Candidate
    )
    $parentCanonical = Resolve-CanonicalPath -Path $Parent
    $candidateCanonical = Resolve-CanonicalPath -Path $Candidate
    return $candidateCanonical.Equals($parentCanonical, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidateCanonical.StartsWith("$parentCanonical\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidateCanonical.StartsWith("$parentCanonical/", [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-ChildPath {
    param(
        [string]$RootPath,
        [string]$Candidate,
        [string]$Label
    )
    $rootCanonical = Resolve-CanonicalPath -Path $RootPath
    $candidateCanonical = Resolve-CanonicalPath -Path $Candidate
    $isChild = $candidateCanonical.Equals($rootCanonical, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidateCanonical.StartsWith("$rootCanonical\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidateCanonical.StartsWith("$rootCanonical/", [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $isChild) {
        throw "$Label must stay under $rootCanonical. Got: $candidateCanonical"
    }
}

function Assert-StrictChildPath {
    param(
        [string]$RootPath,
        [string]$Candidate,
        [string]$Label
    )
    Assert-ChildPath -RootPath $RootPath -Candidate $Candidate -Label $Label
    $rootCanonical = Resolve-CanonicalPath -Path $RootPath
    $candidateCanonical = Resolve-CanonicalPath -Path $Candidate
    if ($candidateCanonical.Equals($rootCanonical, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must be a child directory under $rootCanonical, not the root itself."
    }
}

function Assert-NoPathOverlap {
    param(
        [string]$PathA,
        [string]$PathB,
        [string]$LabelA,
        [string]$LabelB
    )
    if ((Test-SameOrNestedPath -Parent $PathA -Candidate $PathB) -or
        (Test-SameOrNestedPath -Parent $PathB -Candidate $PathA)) {
        throw "$LabelA and $LabelB must not be the same directory or nested. ${LabelA}: $(Resolve-CanonicalPath -Path $PathA) ${LabelB}: $(Resolve-CanonicalPath -Path $PathB)"
    }
}

function Assert-PortableOutputPath {
    param(
        [string]$OutputPath,
        [string[]]$InputDirectories,
        [string[]]$InputFiles
    )
    Assert-StrictChildPath -RootPath $Root -Candidate $OutputPath -Label "OutputDir"
    foreach ($inputPath in $InputDirectories) {
        if (-not $inputPath) {
            continue
        }
        Assert-NoPathOverlap -PathA $OutputPath -PathB $inputPath -LabelA "OutputDir" -LabelB "input path"
    }
    foreach ($inputFile in $InputFiles) {
        if (-not $inputFile) {
            continue
        }
        if (Test-SameOrNestedPath -Parent $OutputPath -Candidate $inputFile) {
            throw "OutputDir must not contain input file: $(Resolve-CanonicalPath -Path $inputFile)"
        }
    }
}

function Get-DirectorySummary {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return [ordered]@{ present = $false; files = 0; bytes = 0; sha256 = "" }
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
    if ([bool]$Expected.present -ne [bool]$Actual.present) { throw "$Label manifest present flag does not match current files." }
    if ([int64]$Expected.files -ne [int64]$Actual.files) { throw "$Label manifest file count does not match current files." }
    if ([int64]$Expected.bytes -ne [int64]$Actual.bytes) { throw "$Label manifest byte count does not match current files." }
    if ([string]$Expected.sha256 -ne [string]$Actual.sha256) { throw "$Label manifest sha256 does not match current files." }
}

function Test-OllamaBundleManifest {
    param(
        [string]$ManifestPath,
        [string]$RuntimeDir,
        [string]$ModelsDir
    )
    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        throw "Bundled Ollama manifest was not found: $ManifestPath. Run scripts\bundle_ollama.ps1 first."
    }
    $manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ([int]$manifest.schema -ne 1) { throw "Unsupported Ollama bundle manifest schema: $($manifest.schema)" }
    if (-not [bool]$manifest.accepted_licenses) { throw "Bundled Ollama manifest must confirm accepted_licenses=true." }
    if (-not [string]$manifest.model) { throw "Bundled Ollama manifest must record the packaged model." }
    if ([string]$manifest.models.model_manifest) {
        $modelManifestPath = Join-Path $ModelsDir ([string]$manifest.models.model_manifest)
        if (-not (Test-Path -LiteralPath $modelManifestPath)) {
            throw "Bundled Ollama model manifest was not found in package source: $modelManifestPath"
        }
    }
    Compare-Summary -Expected $manifest.runtime.summary -Actual (Get-DirectorySummary -Path $RuntimeDir) -Label "Runtime"
    Compare-Summary -Expected $manifest.models.summary -Actual (Get-DirectorySummary -Path $ModelsDir) -Label "Models"
}

$ElectronDist = Join-Path $Root "desktop\node_modules\electron\dist"
$DesktopDist = Join-Path $Root "desktop\dist"
$BackendExe = Resolve-ProjectPath $BackendExe
$Out = Resolve-ProjectPath $OutputDir
$ResolvedOllamaDir = if ($BundledOllamaDir) { Resolve-ProjectPath $BundledOllamaDir } else { "" }
$ResolvedOllamaModelsDir = if ($BundledOllamaModelsDir) { Resolve-ProjectPath $BundledOllamaModelsDir } else { "" }
$ResolvedOllamaManifest = if ($BundledOllamaManifest) { Resolve-ProjectPath $BundledOllamaManifest } else { "" }

if (($ResolvedOllamaDir -or $ResolvedOllamaModelsDir -or $ResolvedOllamaManifest) -and -not ($ResolvedOllamaDir -and $ResolvedOllamaModelsDir -and $ResolvedOllamaManifest)) {
    throw "Bundled Ollama runtime, models, and manifest must be provided explicitly together. Default builds download Ollama and models on demand."
}

Assert-PortableOutputPath -OutputPath $Out -InputDirectories @(
    $ElectronDist,
    $DesktopDist,
    $ResolvedOllamaDir,
    $ResolvedOllamaModelsDir
) -InputFiles @(
    $BackendExe,
    $ResolvedOllamaManifest
)

if ($PathSafetyCheckOnly) {
    Write-Host "Portable path safety check passed for $Out"
    exit 0
}

if (-not (Test-Path $ElectronDist)) {
    throw "Electron runtime was not found at $ElectronDist. Run npm --prefix desktop install first."
}

if (-not (Test-Path $DesktopDist)) {
    throw "Desktop build was not found at $DesktopDist. Run npm --prefix desktop run build first."
}

if (-not (Test-Path $BackendExe)) {
    throw "Backend executable was not found at $BackendExe. Run scripts\build_backend.ps1 first."
}

if (Test-Path $Out) {
    $Resolved = Resolve-Path -LiteralPath $Out
    Assert-PortableOutputPath -OutputPath $Resolved.Path -InputDirectories @(
        $ElectronDist,
        $DesktopDist,
        $ResolvedOllamaDir,
        $ResolvedOllamaModelsDir
) -InputFiles @(
        $BackendExe,
        $ResolvedOllamaManifest
    )
    Remove-Item -LiteralPath $Resolved.Path -Recurse -Force
}

New-Item -ItemType Directory -Path $Out | Out-Null
Copy-Item -Path (Join-Path $ElectronDist "*") -Destination $Out -Recurse -Force

$ElectronExe = Join-Path $Out "electron.exe"
$LengrvisExe = Join-Path $Out "Lengrvis.exe"
if (Test-Path $LengrvisExe) {
    Remove-Item -LiteralPath $LengrvisExe -Force
}
Rename-Item -LiteralPath $ElectronExe -NewName "Lengrvis.exe"

$Resources = Join-Path $Out "resources"
$AppDir = Join-Path $Resources "app"
$AppDistDir = Join-Path $AppDir "dist"
$BackendDir = Join-Path $Resources "backend"
$OllamaOutDir = Join-Path $Resources "ollama"
$OllamaModelsOutDir = Join-Path $Resources "ollama-models"
$OllamaManifestOut = Join-Path $Resources "ollama-bundle-manifest.json"
New-Item -ItemType Directory -Path $AppDistDir -Force | Out-Null
New-Item -ItemType Directory -Path $BackendDir -Force | Out-Null

Copy-Item -Path (Join-Path $DesktopDist "*") -Destination $AppDistDir -Recurse -Force
Copy-Item -Path (Join-Path $Root "desktop\package.json") -Destination $AppDir -Force
Copy-Item -Path $BackendExe -Destination (Join-Path $BackendDir "backend.exe") -Force

if ($ResolvedOllamaDir -and $ResolvedOllamaModelsDir) {
    Test-OllamaBundleManifest -ManifestPath $ResolvedOllamaManifest -RuntimeDir $ResolvedOllamaDir -ModelsDir $ResolvedOllamaModelsDir
}

if ($ResolvedOllamaDir) {
    if (-not (Test-Path $ResolvedOllamaDir)) {
        throw "Bundled Ollama runtime directory was not found: $ResolvedOllamaDir"
    }
    Copy-Item -LiteralPath $ResolvedOllamaDir -Destination $OllamaOutDir -Recurse -Force
    Write-Host "Bundled Ollama runtime copied to $OllamaOutDir"
}

if ($ResolvedOllamaModelsDir) {
    if (-not (Test-Path $ResolvedOllamaModelsDir)) {
        throw "Bundled Ollama models directory was not found: $ResolvedOllamaModelsDir"
    }
    Copy-Item -LiteralPath $ResolvedOllamaModelsDir -Destination $OllamaModelsOutDir -Recurse -Force
    Write-Host "Bundled Ollama models copied to $OllamaModelsOutDir"
}

if ($ResolvedOllamaDir -and $ResolvedOllamaModelsDir) {
    Copy-Item -LiteralPath $ResolvedOllamaManifest -Destination $OllamaManifestOut -Force
    Test-OllamaBundleManifest -ManifestPath $OllamaManifestOut -RuntimeDir $OllamaOutDir -ModelsDir $OllamaModelsOutDir
    Write-Host "Bundled Ollama manifest copied and verified at $OllamaManifestOut"
} else {
    Write-Host "Ollama runtime and models are not bundled; Lengrvis will install/pull them on demand."
}

Write-Host "Portable build created at $Out"
