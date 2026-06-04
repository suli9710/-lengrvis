function Split-TestArguments {
    param([object[]]$RawArgs)

    $coverage = $false
    $pytestArgs = New-Object System.Collections.Generic.List[string]

    for ($i = 0; $i -lt $RawArgs.Count; $i++) {
        $arg = [string]$RawArgs[$i]
        if ($arg -eq "--") {
            for ($j = $i + 1; $j -lt $RawArgs.Count; $j++) {
                $pytestArgs.Add([string]$RawArgs[$j])
            }
            break
        }
        elseif ($arg -ieq "-Coverage") {
            $coverage = $true
        }
        elseif ($arg -imatch "^-Coverage:(true|false)$") {
            $coverage = [System.Convert]::ToBoolean($Matches[1])
        }
        elseif ($arg -ieq "-PytestArgs") {
            for ($j = $i + 1; $j -lt $RawArgs.Count; $j++) {
                $pytestArgs.Add([string]$RawArgs[$j])
            }
            break
        }
        else {
            $pytestArgs.Add($arg)
        }
    }

    return [pscustomobject]@{
        Coverage = $coverage
        PytestArgs = [string[]]$pytestArgs
    }
}

$ParsedArgs = Split-TestArguments -RawArgs $args
$RunTestArgs = @()
if ($ParsedArgs.Coverage) {
    $RunTestArgs += "-Coverage"
}
$RunTestArgs += $ParsedArgs.PytestArgs

& "$PSScriptRoot\run_tests.ps1" @RunTestArgs
exit $LASTEXITCODE
