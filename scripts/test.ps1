param(
    [switch]$Coverage,
    [string[]]$PytestArgs = @()
)

& "$PSScriptRoot\run_tests.ps1" -Coverage:$Coverage -PytestArgs $PytestArgs
exit $LASTEXITCODE
