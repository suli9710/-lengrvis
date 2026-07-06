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

function Invoke-ReleaseRefreshPathSafety {
    param(
        [string]$PortableZip,
        [string]$SelfExtractingExe
    )
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\refresh_portable_release_bundle.ps1" -PortableZip $PortableZip -SelfExtractingExe $SelfExtractingExe 2>&1
        $script:LastReleaseRefreshPathSafetyExitCode = $LASTEXITCODE
        return $output
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Invoke-CSharpSfxPathSafety {
    param([string]$OutputExe)
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\create_csharp_self_extracting_exe.ps1" -PortableZip "dist\missing-portable.zip" -OutputExe $OutputExe 2>&1
        $script:LastCSharpSfxPathSafetyExitCode = $LASTEXITCODE
        return $output
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Assert-ReleaseRefreshFailsWith {
    param(
        [string]$Label,
        [string]$PortableZip,
        [string]$SelfExtractingExe,
        [string]$Expected
    )
    $output = Invoke-ReleaseRefreshPathSafety -PortableZip $PortableZip -SelfExtractingExe $SelfExtractingExe
    if ($script:LastReleaseRefreshPathSafetyExitCode -eq 0) {
        throw "$Label should have failed, but passed."
    }
    $text = ($output | Out-String)
    if ($text -notmatch [regex]::Escape($Expected)) {
        throw "$Label failed with unexpected output. Expected '$Expected'. Got:`n$text"
    }
    Write-Host "[ok] $Label"
}

function Assert-CSharpSfxFailsWith {
    param(
        [string]$Label,
        [string]$OutputExe,
        [string]$Expected
    )
    $output = Invoke-CSharpSfxPathSafety -OutputExe $OutputExe
    if ($script:LastCSharpSfxPathSafetyExitCode -eq 0) {
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
Assert-FailsWith -Label "sibling prefix output refused" -OutputDir "..\lengrvis-evil" -Expected "must stay under"
Assert-FailsWith -Label "renderer build input refused" -OutputDir "desktop\dist" -Expected "must not be the same directory or nested"
Assert-FailsWith -Label "dist input file parent refused" -OutputDir "dist" -Expected "must not contain input file"

$outsideRoot = Join-Path (Split-Path -Parent $Root) "lengrvis-outside-artifacts"
Assert-ReleaseRefreshFailsWith -Label "refresh portable zip outside repo refused" -PortableZip (Join-Path $outsideRoot "portable.zip") -SelfExtractingExe "dist\safe-sfx.exe" -Expected "must stay under repository dist or release directories"
Assert-ReleaseRefreshFailsWith -Label "refresh self-extracting exe outside repo refused" -PortableZip "dist\safe.zip" -SelfExtractingExe (Join-Path $outsideRoot "portable.exe") -Expected "must stay under repository dist or release directories"
Assert-CSharpSfxFailsWith -Label "csharp self-extracting exe outside repo refused" -OutputExe (Join-Path $outsideRoot "direct.exe") -Expected "must stay under repository dist or release directories"

Write-Host ""
Write-Host "Portable path safety smoke passed." -ForegroundColor Green
