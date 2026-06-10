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
    # Empty means: derive dist\Lengrvis-<version>-x64-self-extracting.exe from
    # desktop\package.json inside build_all.ps1 (single source of truth).
    [string]$SelfExtractingExe = "",
    [string]$BundledOllamaDir = "",
    [string]$BundledOllamaModelsDir = "",
    [string]$BundledOllamaManifest = "",
    [string[]]$RequiredBackendCapabilities = @()
)

if ($VerifyOnly) {
    Write-Host "Running packaging verification only; existing release artifacts must already be present."
    Write-Host "Expected portable zip: $PortableZip"
    if ($RunExecutableSmoke) {
        Write-Host "Executable runnable smoke is enabled with timeout $SmokeTimeoutSeconds seconds."
    }
}

& "$PSScriptRoot\build_all.ps1" -SkipTests:$SkipTests -SkipInstaller:$SkipPackageBuild -RequireBundledOllama:$RequireBundledOllama -VerifyOnly:$VerifyOnly -RunExecutableSmoke:$RunExecutableSmoke -SmokeTimeoutSeconds $SmokeTimeoutSeconds -DistDir $DistDir -PortableDir $PortableDir -PortableZip $PortableZip -SelfExtractingExe $SelfExtractingExe -BundledOllamaDir $BundledOllamaDir -BundledOllamaModelsDir $BundledOllamaModelsDir -BundledOllamaManifest $BundledOllamaManifest -RequiredBackendCapabilities $RequiredBackendCapabilities
exit $LASTEXITCODE
