[CmdletBinding()]
param(
    [string]$AuditLevel = "high",
    [switch]$SkipPython
)

# Dependency vulnerability scan (SCA) entrypoint (market-readiness checklist #16).
# Covers npm audit for desktop / mobile and pip-audit for the backend.
# Fail-closed: any finding at or above AuditLevel, or a missing pip-audit
# (without an explicit -SkipPython waiver), exits non-zero.

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

Invoke-NpmAudit "desktop"
Invoke-NpmAudit "mobile"

if (-not $SkipPython) {
    Write-Host ""
    Write-Host "=== pip-audit (backend/requirements-lock.txt) ==="
    $pipAuditAvailable = $true
    try {
        python -m pip_audit --version | Out-Null
        if ($LASTEXITCODE -ne 0) { $pipAuditAvailable = $false }
    }
    catch {
        $pipAuditAvailable = $false
    }

    if (-not $pipAuditAvailable) {
        $failures += "pip-audit is not installed. Install with: python -m pip install pip-audit (or rerun with -SkipPython to record a waiver)."
    }
    else {
        $pipAuditCacheDir = Join-Path $root ".tmp\pip-audit-cache"
        New-Item -ItemType Directory -Path $pipAuditCacheDir -Force | Out-Null
        Push-Location $root
        try {
            python -m pip_audit -r backend/requirements-lock.txt --disable-pip --no-deps --cache-dir $pipAuditCacheDir
            if ($LASTEXITCODE -ne 0) {
                $failures += "pip-audit reported vulnerabilities in backend/requirements-lock.txt"
            }
        }
        finally {
            Pop-Location
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
Write-Host "[pass] dependency audit completed with no findings at level >= $AuditLevel"
exit 0
