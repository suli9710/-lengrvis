[CmdletBinding()]
param(
    [string]$Root = "",
    [switch]$Staged,
    [switch]$History,
    [string]$LogOpts = "--all"
)

$ErrorActionPreference = "Stop"
$gitleaksVersion = "8.30.1"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Join-Path $PSScriptRoot ".."
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
$configPath = Join-Path $resolvedRoot ".gitleaks-ci.toml"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Strict gitleaks config not found: $configPath"
}

$emptyIgnore = Join-Path ([System.IO.Path]::GetTempPath()) ("lengrvis-gitleaks-empty-ignore-{0}.txt" -f ([System.Guid]::NewGuid().ToString("N")))
New-Item -ItemType File -Path $emptyIgnore -Force | Out-Null
$sourceSnapshot = $null

function Invoke-Gitleaks {
    param([string[]]$Arguments)

    $gitleaks = Get-Command gitleaks -ErrorAction SilentlyContinue
    if ($gitleaks) {
        $versionOutput = (& $gitleaks.Source version 2>&1 | Select-Object -First 1).ToString().Trim()
        if ($LASTEXITCODE -ne 0 -or $versionOutput -ne $gitleaksVersion) {
            throw "gitleaks $gitleaksVersion is required for strict scanning; found '$versionOutput' at $($gitleaks.Source)."
        }
        & $gitleaks.Source @Arguments
        return $LASTEXITCODE
    }

    $go = Get-Command go -ErrorAction SilentlyContinue
    if (-not $go) {
        throw "gitleaks or go is required to run secret scanning."
    }
    & $go.Source run "github.com/zricethezav/gitleaks/v8@v$gitleaksVersion" @Arguments
    return $LASTEXITCODE
}

function Copy-GitSourceFile {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$SnapshotRoot,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$RelativePath
    )

    if ([string]::IsNullOrWhiteSpace($RelativePath)) {
        return
    }

    $sourcePath = Join-Path $RepositoryRoot $RelativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        return
    }

    $targetPath = Join-Path $SnapshotRoot $RelativePath
    $targetDir = Split-Path -Parent $targetPath
    if (-not [string]::IsNullOrWhiteSpace($targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
}

function New-SourceSnapshot {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        throw "git is required to build the source snapshot for secret scanning."
    }

    $snapshotRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("lengrvis-gitleaks-source-{0}" -f ([System.Guid]::NewGuid().ToString("N")))
    New-Item -ItemType Directory -Path $snapshotRoot -Force | Out-Null

    try {
        if ($Staged) {
            $checkoutPrefix = $snapshotRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
            & $git.Source -C $RepositoryRoot checkout-index --all --prefix=$checkoutPrefix
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to materialize staged source files for secret scanning."
            }
        }
        else {
            $trackedFilesOutput = & $git.Source -c core.quotepath=false -C $RepositoryRoot ls-files -z --cached
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to enumerate tracked source files for secret scanning."
            }
            $trackedFiles = ($trackedFilesOutput -join "`n") -split "`0"

            foreach ($relativePath in $trackedFiles) {
                Copy-GitSourceFile -RepositoryRoot $RepositoryRoot -SnapshotRoot $snapshotRoot -RelativePath $relativePath
            }
        }

        $untrackedFilesOutput = & $git.Source -c core.quotepath=false -C $RepositoryRoot ls-files -z --others --exclude-standard
        if ($LASTEXITCODE -ne 0) {
            throw "Unable to enumerate untracked source files for secret scanning."
        }
        $sourceFiles = ($untrackedFilesOutput -join "`n") -split "`0"

        foreach ($relativePath in $sourceFiles) {
            Copy-GitSourceFile -RepositoryRoot $RepositoryRoot -SnapshotRoot $snapshotRoot -RelativePath $relativePath
        }
    }
    catch {
        Remove-Item -LiteralPath $snapshotRoot -Recurse -Force -ErrorAction SilentlyContinue
        throw
    }

    return $snapshotRoot
}

Push-Location $resolvedRoot
try {
    $commonArgs = @(
        "--config", $configPath,
        "--gitleaks-ignore-path", $emptyIgnore,
        "--redact",
        "--no-banner"
    )
    if ($History) {
        $gitleaksArgs = @("git") + $commonArgs + @("--log-opts=$LogOpts", ".")
        $exitCode = Invoke-Gitleaks -Arguments $gitleaksArgs
    }
    else {
        $sourceSnapshot = New-SourceSnapshot -RepositoryRoot $resolvedRoot
        $gitleaksArgs = @("dir") + $commonArgs + @($sourceSnapshot)
        $exitCode = Invoke-Gitleaks -Arguments $gitleaksArgs
    }
    if ($exitCode -ne 0) {
        exit $exitCode
    }
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $emptyIgnore -Force -ErrorAction SilentlyContinue
    if (-not [string]::IsNullOrWhiteSpace($sourceSnapshot)) {
        Remove-Item -LiteralPath $sourceSnapshot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Secret scan passed."
