param(
    [switch]$SkipTests,
    [switch]$SkipPackageBuild,
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

if ($VerifyOnly) {
    Write-Host "Running packaging verification only; existing release artifacts must already be present."
    Write-Host "Expected portable zip: $PortableZip"
    if ($RunExecutableSmoke) {
        Write-Host "Executable runnable smoke is enabled with timeout $SmokeTimeoutSeconds seconds."
    }
}

& "$PSScriptRoot\build_all.ps1" -SkipTests:$SkipTests -SkipInstaller:$SkipPackageBuild -RequireBundledOllama:$RequireBundledOllama -VerifyOnly:$VerifyOnly -RunExecutableSmoke:$RunExecutableSmoke -SmokeTimeoutSeconds $SmokeTimeoutSeconds -DistDir $DistDir -PortableDir $PortableDir -PortableZip $PortableZip -SelfExtractingExe $SelfExtractingExe -BundledOllamaDir $BundledOllamaDir -BundledOllamaModelsDir $BundledOllamaModelsDir -BundledOllamaManifest $BundledOllamaManifest
exit $LASTEXITCODE
