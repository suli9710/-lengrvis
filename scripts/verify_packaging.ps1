param(
    [string]$DistDir = "dist",
    [string]$PortableDir = "dist\Lengrvis-win-portable",
    [string]$PortableZip = "dist\Lengrvis-win-portable.zip",
    [string]$SelfExtractingExe = "dist\Lengrvis-0.1.0-x64-self-extracting.exe",
    [switch]$RequireBundledOllama
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Add-Type -AssemblyName System.IO.Compression.FileSystem

$Failures = New-Object System.Collections.Generic.List[string]
$MissingArtifacts = New-Object System.Collections.Generic.List[object]
$MinimumSelfExtractingExeBytes = 65536

function Resolve-ProjectPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Test-RequiredFile([string]$Label, [string]$Path) {
    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        $Failures.Add("$Label missing: $FullPath")
        $MissingArtifacts.Add([pscustomobject]@{ Label = $Label; Path = $FullPath; Type = "file" })
        return
    }
    $Item = Get-Item -LiteralPath $FullPath
    if ($Item.Length -le 0) {
        $Failures.Add("$Label is empty: $FullPath")
        return
    }
    $Version = $Item.VersionInfo.FileVersion
    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = "n/a"
    }
    Write-Host "[ok] $Label ($($Item.Length) bytes, version $Version): $FullPath"
}

function Test-RequiredDirectory([string]$Label, [string]$Path) {
    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Container)) {
        $Failures.Add("$Label missing: $FullPath")
        $MissingArtifacts.Add([pscustomobject]@{ Label = $Label; Path = $FullPath; Type = "directory" })
        return
    }
    Write-Host "[ok] $Label`: $FullPath"
}

function Test-NonEmptyDirectory([string]$Label, [string]$Path) {
    Test-RequiredDirectory $Label $Path
    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Container)) {
        return
    }
    $FirstFile = Get-ChildItem -LiteralPath $FullPath -File -Recurse -Force -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $FirstFile) {
        $Failures.Add("$Label has no files: $FullPath")
        return
    }
    Write-Host "[ok] $Label contains files"
}

function Test-ZipEntry([System.IO.Compression.ZipArchive]$Zip, [string]$EntryName) {
    $Normalized = $EntryName -replace "\\", "/"
    $Entry = $Zip.Entries | Where-Object { ($_.FullName -replace "\\", "/") -eq $Normalized } | Select-Object -First 1
    if ($null -eq $Entry) {
        $Failures.Add("zip entry missing: $Normalized")
        return
    }
    if ($Entry.Length -le 0) {
        $Failures.Add("zip entry is empty: $Normalized")
        return
    }
    Write-Host "[ok] zip entry $Normalized ($($Entry.Length) bytes)"
}

function Test-ZipDirectoryEntry([System.IO.Compression.ZipArchive]$Zip, [string]$Prefix) {
    $Normalized = ($Prefix -replace "\\", "/").TrimEnd("/") + "/"
    $Entry = $Zip.Entries | Where-Object {
        ($_.FullName -replace "\\", "/").StartsWith($Normalized, [System.StringComparison]::OrdinalIgnoreCase) -and
        $_.Length -gt 0
    } | Select-Object -First 1
    if ($null -eq $Entry) {
        $Failures.Add("zip directory missing or empty: $Normalized")
        return
    }
    Write-Host "[ok] zip directory $Normalized contains files"
}

function Test-SelfExtractingExecutable([string]$Path) {
    $FullPath = Resolve-ProjectPath $Path
    if (-not (Test-Path -LiteralPath $FullPath -PathType Leaf)) {
        return
    }

    $Item = Get-Item -LiteralPath $FullPath
    if ($Item.Length -lt $MinimumSelfExtractingExeBytes) {
        $Failures.Add("self-extracting executable is too small to be a release SFX ($($Item.Length) bytes; expected at least $MinimumSelfExtractingExeBytes): $FullPath")
        return
    }

    $Stream = [System.IO.File]::Open($FullPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    $Reader = New-Object System.IO.BinaryReader($Stream)
    try {
        if ($Stream.Length -lt 0x100) {
            $Failures.Add("self-extracting executable is too small for a PE header: $FullPath")
            return
        }

        $Mz = $Reader.ReadUInt16()
        if ($Mz -ne 0x5A4D) {
            $Failures.Add("self-extracting executable does not start with MZ header: $FullPath")
            return
        }

        $Stream.Position = 0x3C
        $PeOffset = [int64]$Reader.ReadUInt32()
        if ($PeOffset -lt 0x40 -or $PeOffset -gt ($Stream.Length - 0x18)) {
            $Failures.Add("self-extracting executable has invalid PE header offset $PeOffset`: $FullPath")
            return
        }

        $Stream.Position = $PeOffset
        $PeSignature = $Reader.ReadUInt32()
        if ($PeSignature -ne 0x00004550) {
            $Failures.Add("self-extracting executable has invalid PE signature at offset $PeOffset`: $FullPath")
            return
        }

        $Machine = $Reader.ReadUInt16()
        if ($Machine -notin @(0x014C, 0x8664, 0xAA64)) {
            $Failures.Add(("self-extracting executable uses unexpected PE machine 0x{0:X4}: {1}" -f $Machine, $FullPath))
        }

        $SectionCount = $Reader.ReadUInt16()
        if ($SectionCount -lt 1 -or $SectionCount -gt 96) {
            $Failures.Add("self-extracting executable has invalid PE section count $SectionCount`: $FullPath")
        }

        $Stream.Position = $PeOffset + 0x14
        $OptionalHeaderSize = $Reader.ReadUInt16()
        if ($OptionalHeaderSize -lt 0x60) {
            $Failures.Add("self-extracting executable optional header is too small ($OptionalHeaderSize bytes): $FullPath")
            return
        }

        $OptionalHeaderOffset = $PeOffset + 0x18
        if (($OptionalHeaderOffset + $OptionalHeaderSize) -gt $Stream.Length) {
            $Failures.Add("self-extracting executable optional header extends beyond file length: $FullPath")
            return
        }

        $Stream.Position = $OptionalHeaderOffset
        $OptionalMagic = $Reader.ReadUInt16()
        if ($OptionalMagic -notin @(0x010B, 0x020B)) {
            $Failures.Add(("self-extracting executable uses unexpected PE optional header magic 0x{0:X4}: {1}" -f $OptionalMagic, $FullPath))
            return
        }

        Write-Host "[ok] self-extracting executable PE header validated ($($Item.Length) bytes): $FullPath"
    }
    finally {
        $Reader.Dispose()
        $Stream.Dispose()
    }
}

function Get-DirectorySummary {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        return [ordered]@{ present = $false; files = 0; bytes = 0; sha256 = "" }
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

function Compare-Summary([object]$Expected, [object]$Actual, [string]$Label) {
    if ([bool]$Expected.present -ne [bool]$Actual.present) { $Failures.Add("$Label manifest present flag does not match package files."); return }
    if ([int64]$Expected.files -ne [int64]$Actual.files) { $Failures.Add("$Label manifest file count does not match package files."); return }
    if ([int64]$Expected.bytes -ne [int64]$Actual.bytes) { $Failures.Add("$Label manifest byte count does not match package files."); return }
    if ([string]$Expected.sha256 -ne [string]$Actual.sha256) { $Failures.Add("$Label manifest sha256 does not match package files."); return }
    Write-Host "[ok] $Label manifest summary matches package files"
}

function Test-OllamaBundleManifest([string]$ManifestPath, [string]$RuntimeDir, [string]$ModelsDir) {
    $FullManifestPath = Resolve-ProjectPath $ManifestPath
    $FullRuntimeDir = Resolve-ProjectPath $RuntimeDir
    $FullModelsDir = Resolve-ProjectPath $ModelsDir
    if (-not (Test-Path -LiteralPath $FullManifestPath -PathType Leaf)) {
        $Failures.Add("portable Ollama bundle manifest missing: $FullManifestPath")
        return
    }
    try {
        $Manifest = Get-Content -LiteralPath $FullManifestPath -Raw | ConvertFrom-Json
    }
    catch {
        $Failures.Add("portable Ollama bundle manifest is not valid JSON: $FullManifestPath")
        return
    }
    if ([int]$Manifest.schema -ne 1) { $Failures.Add("portable Ollama bundle manifest has unsupported schema: $($Manifest.schema)") }
    if (-not [bool]$Manifest.accepted_licenses) { $Failures.Add("portable Ollama bundle manifest must confirm accepted_licenses=true.") }
    if (-not [string]$Manifest.model) { $Failures.Add("portable Ollama bundle manifest must record the packaged model.") }
    if ([string]$Manifest.models.model_manifest) {
        $ModelManifestPath = Join-Path $FullModelsDir ([string]$Manifest.models.model_manifest)
        if (-not (Test-Path -LiteralPath $ModelManifestPath -PathType Leaf)) {
            $Failures.Add("portable Ollama model manifest missing: $ModelManifestPath")
        }
    }
    Compare-Summary -Expected $Manifest.runtime.summary -Actual (Get-DirectorySummary -Path $FullRuntimeDir) -Label "Ollama runtime"
    Compare-Summary -Expected $Manifest.models.summary -Actual (Get-DirectorySummary -Path $FullModelsDir) -Label "Ollama models"
}

$DistPath = Resolve-ProjectPath $DistDir
$PortablePath = Resolve-ProjectPath $PortableDir
$PortableZipPath = Resolve-ProjectPath $PortableZip
$SelfExtractingPath = Resolve-ProjectPath $SelfExtractingExe
$PortableOllamaDir = Join-Path $PortablePath "resources\ollama"
$PortableOllamaModelsDir = Join-Path $PortablePath "resources\ollama-models"
$PortableOllamaManifest = Join-Path $PortablePath "resources\ollama-bundle-manifest.json"

Test-RequiredDirectory "dist directory" $DistPath
Test-RequiredFile "backend executable" (Join-Path $DistPath "backend.exe")
Test-RequiredDirectory "portable directory" $PortablePath
Test-RequiredFile "portable launcher" (Join-Path $PortablePath "Lengrvis.exe")
Test-RequiredFile "portable backend executable" (Join-Path $PortablePath "resources\backend\backend.exe")
Test-RequiredDirectory "portable app resources" (Join-Path $PortablePath "resources\app")
Test-RequiredDirectory "portable renderer dist" (Join-Path $PortablePath "resources\app\dist")
Test-RequiredFile "portable app package manifest" (Join-Path $PortablePath "resources\app\package.json")
Test-RequiredFile "portable zip" $PortableZipPath
Test-RequiredFile "self-extracting executable" $SelfExtractingPath
Test-SelfExtractingExecutable $SelfExtractingPath
if ($RequireBundledOllama) {
    Test-NonEmptyDirectory "portable Ollama runtime" $PortableOllamaDir
    Test-NonEmptyDirectory "portable Ollama models" $PortableOllamaModelsDir
    Test-RequiredFile "portable Ollama bundle manifest" $PortableOllamaManifest
    Test-OllamaBundleManifest -ManifestPath $PortableOllamaManifest -RuntimeDir $PortableOllamaDir -ModelsDir $PortableOllamaModelsDir
}

if (Test-Path -LiteralPath $PortableZipPath -PathType Leaf) {
    $Zip = [System.IO.Compression.ZipFile]::OpenRead($PortableZipPath)
    try {
        Test-ZipEntry $Zip "Lengrvis.exe"
        Test-ZipEntry $Zip "resources/backend/backend.exe"
        Test-ZipEntry $Zip "resources/app/package.json"
        if ($RequireBundledOllama) {
            Test-ZipEntry $Zip "resources/ollama-bundle-manifest.json"
            Test-ZipDirectoryEntry $Zip "resources/ollama"
            Test-ZipDirectoryEntry $Zip "resources/ollama-models"
        }
    }
    finally {
        $Zip.Dispose()
    }
}

if ($Failures.Count -gt 0) {
    Write-Host ""
    Write-Host "Packaging verification failed:" -ForegroundColor Red
    foreach ($Failure in $Failures) {
        Write-Host " - $Failure" -ForegroundColor Red
    }
    if ($MissingArtifacts.Count -gt 0) {
        Write-Host ""
        Write-Host "Missing release artifacts are blocking the gate:" -ForegroundColor Yellow
        foreach ($Artifact in $MissingArtifacts) {
            Write-Host " - $($Artifact.Label) ($($Artifact.Type)): $($Artifact.Path)" -ForegroundColor Yellow
        }
        Write-Host ""
        Write-Host "This verification step does not generate artifacts. Run a full packaging build first, or pass -DistDir/-PortableDir/-PortableZip/-SelfExtractingExe to the artifacts you intend to release." -ForegroundColor Yellow
    }
    exit 1
}

Write-Host ""
Write-Host "Packaging verification passed." -ForegroundColor Green
