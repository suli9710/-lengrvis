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

function Test-ExplicitPytestTarget {
    param([string[]]$PytestArgs)

    foreach ($arg in $PytestArgs) {
        if ([string]::IsNullOrWhiteSpace($arg) -or $arg.StartsWith("-")) {
            continue
        }

        $candidate = ($arg -split "::", 2)[0]
        if (Test-Path -LiteralPath $candidate) {
            return $true
        }
    }

    return $false
}

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (Test-Path ".venv\Scripts\Activate.ps1") {
    . ".venv\Scripts\Activate.ps1"
}

$ParsedArgs = Split-TestArguments -RawArgs $args

$ArgsList = @()
if (-not (Test-ExplicitPytestTarget -PytestArgs $ParsedArgs.PytestArgs)) {
    $ArgsList += "backend/tests"
}
if ($ParsedArgs.Coverage) {
    $ArgsList += @("--cov=backend", "--cov-report=term-missing")
}
$ArgsList += $ParsedArgs.PytestArgs

python -m pytest @ArgsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path "desktop\package.json") {
    npm --prefix desktop run typecheck
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (Test-Path "mobile\package.json") {
    npm --prefix mobile run typecheck
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:token
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:remote-input-grant
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
