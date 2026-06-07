param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$RequireBundledOllama,
    [switch]$VerifyOnly,
    [switch]$RunExecutableSmoke,
    [ValidateRange(1, 300)]
    [int]$SmokeTimeoutSeconds = 30,
    [string]$DistDir = "dist",
    [string]$PortableDir = "dist\Lengrvis-win-portable",
    [string]$PortableZip = "dist\Lengrvis-win-portable.zip",
    [string]$SelfExtractingExe = "dist\Lengrvis-0.1.0-x64-self-extracting.exe",
    [string]$BundledOllamaDir = "",
    [string]$BundledOllamaModelsDir = "",
    [string]$BundledOllamaManifest = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Invoke-PackagingVerification {
    $verifyArgs = @{
        DistDir = $DistDir
        PortableDir = $PortableDir
        PortableZip = $PortableZip
        SelfExtractingExe = $SelfExtractingExe
        SmokeTimeoutSeconds = $SmokeTimeoutSeconds
    }
    if ($RequireBundledOllama) {
        $verifyArgs.RequireBundledOllama = $true
    }
    if ($RunExecutableSmoke) {
        $verifyArgs.RunExecutableSmoke = $true
    }
    & "$PSScriptRoot\verify_packaging.ps1" @verifyArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($VerifyOnly) {
    Invoke-PackagingVerification
    exit 0
}

if (-not $SkipTests) {
    & "$PSScriptRoot\run_tests.ps1"
}

$DistPath = Resolve-ProjectPath $DistDir
$PortablePath = Resolve-ProjectPath $PortableDir
$PortableZipPath = Resolve-ProjectPath $PortableZip
$SelfExtractingPath = Resolve-ProjectPath $SelfExtractingExe

& "$PSScriptRoot\build_backend.ps1" -DistDir $DistDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($SkipInstaller) {
    npm --prefix desktop run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    npm --prefix desktop install
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix desktop run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $portableArgs = @{
        OutputDir = $PortableDir
        BackendExe = (Join-Path $DistPath "backend.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace($BundledOllamaDir)) {
        $portableArgs.BundledOllamaDir = $BundledOllamaDir
    }
    if (-not [string]::IsNullOrWhiteSpace($BundledOllamaModelsDir)) {
        $portableArgs.BundledOllamaModelsDir = $BundledOllamaModelsDir
    }
    if (-not [string]::IsNullOrWhiteSpace($BundledOllamaManifest)) {
        $portableArgs.BundledOllamaManifest = $BundledOllamaManifest
    }
    & "$PSScriptRoot\build_portable.ps1" @portableArgs
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $PortableZipParent = Split-Path -Parent $PortableZipPath
    if ($PortableZipParent) {
        New-Item -ItemType Directory -Path $PortableZipParent -Force | Out-Null
    }
    if (Test-Path -LiteralPath $PortableZipPath) {
        Remove-Item -LiteralPath $PortableZipPath -Force
    }
    Compress-Archive -Path (Join-Path $PortablePath "*") -DestinationPath $PortableZipPath -CompressionLevel Optimal
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $SelfExtractingParent = Split-Path -Parent $SelfExtractingPath
    if ($SelfExtractingParent) {
        New-Item -ItemType Directory -Path $SelfExtractingParent -Force | Out-Null
    }
    & "$PSScriptRoot\create_csharp_self_extracting_exe.ps1" -PortableZip $PortableZip -OutputExe $SelfExtractingExe
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Invoke-PackagingVerification
}
