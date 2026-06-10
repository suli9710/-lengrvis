[CmdletBinding()]
param(
    [double]$MinPassRate = 0.95,
    [string]$ReportDir = ""
)

# Golden-task regression gate (market-readiness checklist items #3 / #7,
# machine-verified part). Runs backend/tests/test_golden_tasks.py, writes a
# pass-rate report, and fails below the threshold.
# Boundary: machine self-verified regression evidence only -- not a human
# result-quality review and not an RC sign-off.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

if ([string]::IsNullOrWhiteSpace($ReportDir)) {
    $ReportDir = Join-Path $root ".tmp\qa-evidence\golden-tasks"
}
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null

$junitPath = Join-Path $ReportDir "golden-tasks-junit.xml"
$reportPath = Join-Path $ReportDir "golden-tasks-report.json"
$command = "python -m pytest backend/tests/test_golden_tasks.py -q --junit-xml `"$junitPath`""

Push-Location $root
try {
    python -m pytest backend/tests/test_golden_tasks.py -q --junit-xml $junitPath
    $pytestExit = $LASTEXITCODE
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $junitPath)) {
    Write-Error "golden-tasks junit report was not produced; pytest exit=$pytestExit"
    exit 1
}

[xml]$junit = Get-Content -LiteralPath $junitPath -Raw
$suite = $junit.testsuites.testsuite
if (-not $suite) { $suite = $junit.testsuite }
$total = [int]$suite.tests
$failures = [int]$suite.failures + [int]$suite.errors
$skipped = [int]$suite.skipped
$passed = $total - $failures - $skipped
$rate = if ($total -gt 0) { [math]::Round($passed / $total, 4) } else { 0 }
$gatePass = ($pytestExit -eq 0) -and ($rate -ge $MinPassRate)

$report = [ordered]@{
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    command          = $command
    dataset          = "test_data/golden_tasks/golden_tasks.json"
    total            = $total
    passed           = $passed
    failed           = $failures
    skipped          = $skipped
    pass_rate        = $rate
    min_pass_rate    = $MinPassRate
    gate_pass        = $gatePass
    evidence_boundary = "Machine self-verified regression evidence only. Not a human result-quality review, not clean-machine evidence, not an RC sign-off. Human review flow: docs/qa/golden-tasks.md + npm run evidence:result-quality-review."
}
$report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $reportPath -Encoding UTF8

Write-Host ""
Write-Host ("golden tasks: {0}/{1} passed (rate={2:P1}, threshold={3:P0})" -f $passed, $total, $rate, $MinPassRate)
Write-Host ("report: {0}" -f $reportPath)

if (-not $gatePass) {
    Write-Error "golden task gate failed (pytest exit=$pytestExit, pass rate=$rate, threshold=$MinPassRate)"
    exit 1
}
exit 0
