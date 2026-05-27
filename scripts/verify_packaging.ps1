param(
    [string]$DistDir = "dist",
    [string]$PortableDir = "dist\Mavris-win-portable",
    [string]$PortableZip = "dist\Mavris-win-portable.zip",
    [string]$SelfExtractingExe = "dist\Mavris-0.1.0-x64-self-extracting.exe"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Add-Type -AssemblyName System.IO.Compression.FileSystem

$Failures = New-Object System.Collections.Generic.List[string]

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
        return
    }
    Write-Host "[ok] $Label`: $FullPath"
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

$DistPath = Resolve-ProjectPath $DistDir
$PortablePath = Resolve-ProjectPath $PortableDir
$PortableZipPath = Resolve-ProjectPath $PortableZip
$SelfExtractingPath = Resolve-ProjectPath $SelfExtractingExe

Test-RequiredDirectory "dist directory" $DistPath
Test-RequiredFile "backend executable" (Join-Path $DistPath "backend.exe")
Test-RequiredDirectory "portable directory" $PortablePath
Test-RequiredFile "portable launcher" (Join-Path $PortablePath "Mavris.exe")
Test-RequiredFile "portable backend executable" (Join-Path $PortablePath "resources\backend\backend.exe")
Test-RequiredDirectory "portable app resources" (Join-Path $PortablePath "resources\app")
Test-RequiredDirectory "portable renderer dist" (Join-Path $PortablePath "resources\app\dist")
Test-RequiredFile "portable app package manifest" (Join-Path $PortablePath "resources\app\package.json")
Test-RequiredFile "portable zip" $PortableZipPath
Test-RequiredFile "self-extracting executable" $SelfExtractingPath

if (Test-Path -LiteralPath $PortableZipPath -PathType Leaf) {
    $Zip = [System.IO.Compression.ZipFile]::OpenRead($PortableZipPath)
    try {
        Test-ZipEntry $Zip "Mavris.exe"
        Test-ZipEntry $Zip "resources/backend/backend.exe"
        Test-ZipEntry $Zip "resources/app/package.json"
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
    exit 1
}

Write-Host ""
Write-Host "Packaging verification passed." -ForegroundColor Green
