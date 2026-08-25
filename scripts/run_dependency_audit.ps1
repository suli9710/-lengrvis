[CmdletBinding()]
param(
    [string]$AuditLevel = "high",
    [switch]$SkipPython,
    [ValidateRange(1, 1800)]
    [int]$PythonAuditTimeoutSeconds = 180
)

# Dependency vulnerability scan (SCA) entrypoint (market-readiness checklist #16).
# Covers npm audit for workspace QA / desktop / mobile and pip-audit for backend runtime,
# development, backend build, and optional acceleration locks. npm uses AuditLevel; Python
# audit fails closed on any pip-audit finding or audit error.

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$failures = @()

function Invoke-NpmAudit([string]$Prefix) {
    Write-Host ""
    Write-Host "=== npm audit ($Prefix, level=$AuditLevel) ==="
    Push-Location (Join-Path $root $Prefix)
    try {
        npm audit --audit-level=$AuditLevel
        if ($LASTEXITCODE -ne 0) {
            $script:failures += "npm audit ($Prefix) reported vulnerabilities at level >= $AuditLevel"
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-PipAudit([string]$WorkingDirectory, [string]$LogDirectory, [string]$RequirementsPath) {
    $logName = ($RequirementsPath -replace "[\\/:\s]+", "-")
    $stdoutPath = Join-Path $LogDirectory "$logName.stdout.log"
    $stderrPath = Join-Path $LogDirectory "$logName.stderr.log"
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = "python"
    $startInfo.Arguments = "-m pip_audit -r `"$RequirementsPath`" --disable-pip --no-deps"
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $timeoutMilliseconds = $PythonAuditTimeoutSeconds * 1000
    $completed = $process.WaitForExit($timeoutMilliseconds)
    if (-not $completed) {
        try {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit()
        }
        catch {
            Write-Warning "Unable to stop timed-out pip-audit process: $($_.Exception.Message)"
        }
    }
    else {
        # Ensure redirected streams are flushed and ExitCode is populated on Windows.
        $process.WaitForExit()
        $process.Refresh()
    }

    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()
    Set-Content -LiteralPath $stdoutPath -Value $stdout -Encoding utf8
    Set-Content -LiteralPath $stderrPath -Value $stderr -Encoding utf8

    foreach ($path in @($stdoutPath, $stderrPath)) {
        if (Test-Path -LiteralPath $path) {
            Get-Content -LiteralPath $path | ForEach-Object { Write-Host $_ }
        }
    }
    return [pscustomobject]@{
        Completed = $completed
        ExitCode = if ($completed) { $process.ExitCode } else { $null }
    }
}

Invoke-NpmAudit "."
Invoke-NpmAudit "desktop"
Invoke-NpmAudit "mobile"

if (-not $SkipPython) {
    $pipAuditAvailable = $true
    try {
        python -m pip_audit --version | Out-Null
        if ($LASTEXITCODE -ne 0) { $pipAuditAvailable = $false }
    }
    catch {
        $pipAuditAvailable = $false
    }

    if (-not $pipAuditAvailable) {
        $failures += "pip-audit is not installed. Install with: python -m pip install --require-hashes -r requirements-dev-lock.txt (or rerun with -SkipPython to record a waiver)."
    }
    else {
        $pipAuditLogDir = Join-Path $root ".tmp\pip-audit-cache"
        New-Item -ItemType Directory -Path $pipAuditLogDir -Force | Out-Null
        $pythonLocks = @(
            "backend/requirements-lock.txt",
            "requirements-dev-lock.txt",
            "backend/requirements-build-lock.txt",
            "scripts/acceleration-requirements-lock.txt"
        )
        foreach ($pythonLock in $pythonLocks) {
            Write-Host ""
            Write-Host "=== pip-audit ($pythonLock) ==="
            $lockPath = Join-Path $root $pythonLock
            if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) {
                $failures += "pip-audit lock file is missing: $pythonLock"
                continue
            }
            $auditResult = Invoke-PipAudit $root $pipAuditLogDir $pythonLock
            if (-not $auditResult.Completed) {
                $failures += "pip-audit timed out after $PythonAuditTimeoutSeconds seconds for ${pythonLock}; inspect .tmp\\pip-audit-cache for diagnostics."
            }
            elseif ($auditResult.ExitCode -ne 0) {
                $failures += "pip-audit reported vulnerabilities or an audit error in $pythonLock"
            }
        }
    }
}
else {
    Write-Warning "Python dependency audit skipped (-SkipPython). Record this as a waiver in the QA handoff."
}

Write-Host ""
if ($failures.Count -gt 0) {
    foreach ($failure in $failures) { Write-Host "[fail] $failure" }
    Write-Error "dependency audit failed ($($failures.Count) finding group(s))"
    exit 1
}
Write-Host "[pass] dependency audit completed with no npm findings at level >= $AuditLevel and no pip-audit findings"
exit 0
