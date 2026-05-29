param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$RequireBundledOllama,
    [switch]$VerifyOnly,
    [string]$DistDir = "dist",
    [string]$PortableDir = "dist\Mavris-win-portable",
    [string]$PortableZip = "dist\Mavris-win-portable.zip",
    [string]$SelfExtractingExe = "dist\Mavris-0.1.0-x64-self-extracting.exe"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Invoke-PackagingVerification {
    if ($RequireBundledOllama) {
        & "$PSScriptRoot\verify_packaging.ps1" -DistDir $DistDir -PortableDir $PortableDir -PortableZip $PortableZip -SelfExtractingExe $SelfExtractingExe -RequireBundledOllama
    }
    else {
        & "$PSScriptRoot\verify_packaging.ps1" -DistDir $DistDir -PortableDir $PortableDir -PortableZip $PortableZip -SelfExtractingExe $SelfExtractingExe
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($VerifyOnly) {
    Invoke-PackagingVerification
    exit 0
}

if (-not $SkipTests) {
    & "$PSScriptRoot\run_tests.ps1"
}

& "$PSScriptRoot\build_backend.ps1"

if ($SkipInstaller) {
    npm --prefix desktop run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
else {
    npm --prefix desktop install
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix desktop run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & "$PSScriptRoot\build_portable.ps1"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $PortableZip = Join-Path $Root "dist\Mavris-win-portable.zip"
    if (Test-Path $PortableZip) {
        Remove-Item -LiteralPath $PortableZip -Force
    }
    Compress-Archive -Path (Join-Path $Root "dist\Mavris-win-portable\*") -DestinationPath $PortableZip -CompressionLevel Optimal
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & "$PSScriptRoot\create_csharp_self_extracting_exe.ps1"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Invoke-PackagingVerification
}
