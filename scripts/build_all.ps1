param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

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
}
