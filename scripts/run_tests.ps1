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
$HasExplicitPytestTarget = Test-ExplicitPytestTarget -PytestArgs $ParsedArgs.PytestArgs

function Test-PytestXdistAvailable {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        python -c "import xdist" 2>$null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

$ArgsList = @()
if (-not $HasExplicitPytestTarget) {
    $ArgsList += @("backend/tests")
    if (Test-PytestXdistAvailable) {
        $ArgsList += @("-n", "auto")
    }
}
if ($ParsedArgs.Coverage) {
    $ArgsList += @("--cov=backend", "--cov-report=term-missing")
    if (-not $HasExplicitPytestTarget) {
        $ArgsList += @("--cov-fail-under=75")
    }
}
$ArgsList += $ParsedArgs.PytestArgs

python -m pytest @ArgsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path "desktop\package.json") {
    npm --prefix desktop run typecheck
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix desktop test
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (Test-Path "mobile\package.json") {
    Push-Location "mobile"
    try {
        npm exec expo -- install --check
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }

    npm --prefix mobile run typecheck
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:token
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:task-companion
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:remote-input-grant
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:wakeup-contract
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:android-back
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:approval-status-label
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:android-hardening-plugin
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    npm --prefix mobile run smoke:android-lan-tls
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (-not $env:ANDROID_HOME -or -not (Test-Path -LiteralPath $env:ANDROID_HOME)) {
        Write-Error "ANDROID_HOME is required to compile the Android companion."
        exit 1
    }
    Push-Location "mobile\android"
    try {
        .\gradlew.bat :app:assembleDebug :app:assembleDebugAndroidTest --no-daemon --stacktrace
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    finally {
        Pop-Location
    }
}
