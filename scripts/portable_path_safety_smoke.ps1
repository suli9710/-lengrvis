param(
    [string]$SafeOutputDir = ".tmp\portable-path-safety-ok"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

function Invoke-PortablePathSafety {
    param([string]$OutputDir)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\build_portable.ps1" -OutputDir $OutputDir -PathSafetyCheckOnly 2>&1
        $script:LastPortablePathSafetyExitCode = $LASTEXITCODE
        return $output
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Assert-Passes {
    param(
        [string]$Label,
        [string]$OutputDir
    )
    $output = Invoke-PortablePathSafety -OutputDir $OutputDir
    if ($script:LastPortablePathSafetyExitCode -ne 0) {
        throw "$Label should have passed, but failed:`n$output"
    }
    Write-Host "[ok] $Label"
}

function Assert-FailsWith {
    param(
        [string]$Label,
        [string]$OutputDir,
        [string]$Expected
    )
    $output = Invoke-PortablePathSafety -OutputDir $OutputDir
    if ($script:LastPortablePathSafetyExitCode -eq 0) {
        throw "$Label should have failed, but passed."
    }
    $text = ($output | Out-String)
    if ($text -notmatch [regex]::Escape($Expected)) {
        throw "$Label failed with unexpected output. Expected '$Expected'. Got:`n$text"
    }
    Write-Host "[ok] $Label"
}

Assert-Passes -Label "safe child output" -OutputDir $SafeOutputDir
Assert-FailsWith -Label "repo root output refused" -OutputDir "." -Expected "not the root itself"
Assert-FailsWith -Label "sibling prefix output refused" -OutputDir "..\mavris-evil" -Expected "must stay under"
Assert-FailsWith -Label "renderer build input refused" -OutputDir "desktop\dist" -Expected "must not be the same directory or nested"
Assert-FailsWith -Label "dist input file parent refused" -OutputDir "dist" -Expected "must not contain input file"

Write-Host ""
Write-Host "Portable path safety smoke passed." -ForegroundColor Green
