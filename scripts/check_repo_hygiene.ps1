[CmdletBinding()]
param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$git = Get-Command git -ErrorAction SilentlyContinue
if ($null -eq $git) {
    Write-Host "Repo hygiene check failed: git is required." -ForegroundColor Red
    exit 1
}

# Keep this list narrow so vendored code and fixture trees do not cause false positives.
$blockedPathspecs = @(
    ".codex-vite*",
    "pytest-backend.log",
    ".claude/settings.local.json",
    ".codex_remote/",
    ".tmp/",
    "tmp-office-review/",
    "desktop/tmp-office-review/",
    "UsersSuliAppDataLocalTempClaude-Code-review",
    "UsersSuliAppDataLocalTempClaude-Code-review/"
)

$trackedArtifacts = & git -C $resolvedRoot ls-files -- $blockedPathspecs
if ($LASTEXITCODE -ne 0) {
    Write-Host "Repo hygiene check failed: unable to inspect tracked files." -ForegroundColor Red
    exit $LASTEXITCODE
}

$violations = @(
    $trackedArtifacts |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique
)

if ($violations.Count -gt 0) {
    Write-Host "Repo hygiene check failed: local runtime artifacts are tracked:" -ForegroundColor Red
    foreach ($violation in $violations) {
        Write-Host " - $violation" -ForegroundColor Red
    }
    exit 1
}

Write-Host "Repo hygiene check passed: no tracked local runtime artifacts."
