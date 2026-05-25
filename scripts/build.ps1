param(
    [switch]$SkipTests,
    [switch]$SkipPackageBuild
)

& "$PSScriptRoot\build_all.ps1" -SkipTests:$SkipTests -SkipInstaller:$SkipPackageBuild
exit $LASTEXITCODE
