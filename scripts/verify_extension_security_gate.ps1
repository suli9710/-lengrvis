[CmdletBinding()]
param(
    [string]$Root = "",
    [string]$OutputRoot = ""
)

$ErrorActionPreference = "Stop"

try {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = $utf8NoBom
    [Console]::InputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
}
catch {
}

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}
$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path

if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $resolvedRoot ".tmp\extension-security-gate"
}
elseif (-not [System.IO.Path]::IsPathRooted($OutputRoot)) {
    $OutputRoot = Join-Path $resolvedRoot $OutputRoot
}

$runId = "run-{0}" -f (Get-Date -Format "yyyyMMdd-HHmmss")
$runRoot = Join-Path $OutputRoot $runId
New-Item -ItemType Directory -Path $runRoot -Force | Out-Null

function Get-CommandPathOrName([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return $Name
    }
    return $command.Source
}

function Get-PythonCommand {
    $venvPython = Join-Path $resolvedRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }
    return Get-CommandPathOrName "python"
}

function Invoke-GateCommand([string]$Name, [string]$FilePath, [string[]]$Arguments) {
    Push-Location $resolvedRoot
    try {
        $startedAt = (Get-Date).ToUniversalTime().ToString("o")
        $output = @(& $FilePath @Arguments 2>&1)
        $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
        $finishedAt = (Get-Date).ToUniversalTime().ToString("o")
        foreach ($line in $output) {
            Write-Host ([string]$line)
        }
        $result = "failed"
        if ($exitCode -eq 0) {
            $result = "passed"
        }
        return [ordered]@{
            name = $Name
            command = ($FilePath + " " + ($Arguments -join " ")).Trim()
            exit_code = $exitCode
            result = $result
            started_at_utc = $startedAt
            finished_at_utc = $finishedAt
            output_tail = @($output | Select-Object -Last 40 | ForEach-Object { [string]$_ })
        }
    }
    finally {
        Pop-Location
    }
}

$python = Get-PythonCommand
$npm = Get-CommandPathOrName "npm"
$node = Get-CommandPathOrName "node"

$commands = @()
$commands += Invoke-GateCommand "backend Skill/MCP/settings security tests" $python @(
    "-m",
    "pytest",
    "backend/tests/test_app_skill_protocol.py",
    "backend/tests/test_skill_routes.py",
    "backend/tests/test_app_skill_packages.py",
    "backend/tests/test_mcp_client.py",
    "backend/tests/test_mcp_ssrf.py",
    "backend/tests/test_resilience_settings.py",
    "backend/tests/test_state_machine_integration.py",
    "-q"
)
$commands += Invoke-GateCommand "desktop Electron build for IPC smoke" $npm @("--prefix", "desktop", "run", "build:electron")
$commands += Invoke-GateCommand "desktop IPC security smoke" $node @("desktop/scripts/ipc-security-smoke.cjs")

$failed = @($commands | Where-Object { $_.exit_code -ne 0 })
$passed = $failed.Count -eq 0
$status = "failed"
if ($passed) {
    $status = "passed"
}

$summary = [ordered]@{
    generated_by = "scripts/verify_extension_security_gate.ps1"
    generated_at_utc = (Get-Date).ToUniversalTime().ToString("o")
    status = $status
    claim_controls = [ordered]@{
        extension_security_gate_passed = $passed
        skill_signature_verification_checked = $passed
        skill_permission_and_upgrade_diff_checked = $passed
        mcp_schema_and_ssrf_checked = $passed
        settings_sensitive_enforcement_checked = $passed
        ipc_policy_and_external_url_checked = $passed
        release_signoff = $false
    }
    mechanisms = [ordered]@{
        ipc_policy_manifest = "desktop/src/shared/ipc.ts::IPC_CHANNEL_SECURITY_POLICIES"
        external_url_policy = "desktop/src/main/ipc.ts::isSafeExternalUrl"
        skill_signature_verification = "backend/app/skills/loader.py::verify_skill_signature"
        skill_upgrade_diff_audit = "backend/app/services/skill_service.py::_package_upgrade_diff"
        mcp_input_schema_validation = "backend/app/mcp/client.py::_validate_tool_arguments"
        settings_sensitive_confirmation = "backend/app/security/sensitive_confirmation.py::require_settings_confirmation"
    }
    commands = @($commands)
    failed_items = @($failed | ForEach-Object { "$($_.name): exit $($_.exit_code)" })
    manual_followups = @(
        "Verify at least one signed production Skill package with the real release signing key.",
        "Verify at least one third-party MCP server under an owner-approved permission policy.",
        "Review the audit log chain for skills.imported and mcp.tool_call_blocked entries on the candidate profile."
    )
}

$jsonPath = Join-Path $runRoot "extension-security-gate.redacted.json"
$mdPath = Join-Path $runRoot "extension-security-gate.redacted.md"
$summary | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $jsonPath -Encoding UTF8

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add("# Extension Security Gate")
$lines.Add("")
$lines.Add("- Status: $($summary.status)")
$lines.Add("- Generated at UTC: $($summary.generated_at_utc)")
$lines.Add("- Release sign-off: false")
$lines.Add("")
$lines.Add("## Commands")
$lines.Add("")
foreach ($command in $commands) {
    $lines.Add("- [$($command.result)] ``$($command.command)``")
}
$lines.Add("")
$lines.Add("## Mechanisms")
$lines.Add("")
foreach ($property in $summary.mechanisms.GetEnumerator()) {
    $lines.Add("- $($property.Key): ``$($property.Value)``")
}
$lines.Add("")
$lines.Add("## Failed Items")
$lines.Add("")
if ($summary.failed_items.Count -eq 0) {
    $lines.Add("- None")
}
else {
    foreach ($item in $summary.failed_items) {
        $lines.Add("- $item")
    }
}
$lines.Add("")
$lines.Add("## Manual Followups")
$lines.Add("")
foreach ($item in $summary.manual_followups) {
    $lines.Add("- $item")
}
$lines.Add("")
[System.IO.File]::WriteAllText($mdPath, ($lines -join "`n"), (New-Object System.Text.UTF8Encoding $false))

Write-Host "Extension security gate summary: $jsonPath"
Write-Host "Extension security gate markdown: $mdPath"

if (-not $passed) {
    exit 1
}
