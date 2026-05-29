param(
    [switch]$SkipTests,
    [switch]$SkipPackageBuild,
    [switch]$RequireBundledOllama,
    [switch]$VerifyOnly,
    [string]$DistDir = "dist",
    [string]$PortableDir = "dist\Mavris-win-portable",
    [string]$PortableZip = "dist\Mavris-win-portable.zip",
    [string]$SelfExtractingExe = "dist\Mavris-0.1.0-x64-self-extracting.exe"
)

& "$PSScriptRoot\build_all.ps1" -SkipTests:$SkipTests -SkipInstaller:$SkipPackageBuild -RequireBundledOllama:$RequireBundledOllama -VerifyOnly:$VerifyOnly -DistDir $DistDir -PortableDir $PortableDir -PortableZip $PortableZip -SelfExtractingExe $SelfExtractingExe
exit $LASTEXITCODE
