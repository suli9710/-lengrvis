param(
    [string]$Workspace = ".tmp\self-extracting-launcher-smoke"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

function Resolve-SmokePath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Assert-Smoke {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
}

function Assert-DoesNotMatch {
    param(
        [string]$Value,
        [string]$Pattern,
        [string]$Message
    )
    if ($Value -match $Pattern) {
        throw $Message
    }
}

$workspacePath = Resolve-SmokePath $Workspace
if (Test-Path -LiteralPath $workspacePath) {
    $resolvedWorkspace = (Resolve-Path -LiteralPath $workspacePath).Path
    Assert-Smoke ($resolvedWorkspace.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) "Refusing to remove smoke workspace outside repo: $resolvedWorkspace"
    Remove-Item -LiteralPath $resolvedWorkspace -Recurse -Force
}
New-Item -ItemType Directory -Path $workspacePath -Force | Out-Null

$scriptPath = Join-Path $Root "scripts\create_self_extracting_exe.ps1"
$source = [System.IO.File]::ReadAllText($scriptPath, [System.Text.Encoding]::UTF8)

Assert-DoesNotMatch $source "Expand-Archive\s+-LiteralPath\s+'%~dp0" "Legacy launcher still embeds `%~dp0 inside a PowerShell single-quoted string."
Assert-DoesNotMatch $source "DestinationPath\s+'%TARGET%'" "Legacy launcher still embeds `%TARGET% inside a PowerShell single-quoted string."
Assert-DoesNotMatch $source '-Command\s+"Expand-Archive' "Legacy launcher still uses inline -Command path interpolation."
Assert-Smoke ($source -match "LENGRVIS_SFX_PAYLOAD_ZIP") "Launcher no longer exposes payload zip via environment variable."
Assert-Smoke ($source -match "LENGRVIS_SFX_TARGET") "Launcher no longer exposes target path via environment variable."
Assert-Smoke ($source -match "-EncodedCommand") "Launcher no longer uses EncodedCommand for the PowerShell payload."

$match = [System.Text.RegularExpressions.Regex]::Match(
    $source,
    "\`$LauncherPowerShell\s*=\s*@'\r?\n(?<command>[\s\S]*?)\r?\n'@"
)
Assert-Smoke $match.Success "Could not extract LauncherPowerShell from create_self_extracting_exe.ps1."
$launcherPowerShell = $match.Groups["command"].Value
Assert-Smoke ($launcherPowerShell -match '\$env:LENGRVIS_SFX_PAYLOAD_ZIP') "Launcher PowerShell does not read the payload zip from the environment."
Assert-Smoke ($launcherPowerShell -match '\$env:LENGRVIS_SFX_TARGET') "Launcher PowerShell does not read the target path from the environment."
Assert-DoesNotMatch $launcherPowerShell "%~dp0|%TARGET%" "Launcher PowerShell still contains cmd-expanded path placeholders."

$payloadSource = Join-Path $workspacePath "payload-source"
$payloadZipDir = Join-Path $workspacePath "Zip's Dir"
$payloadZip = Join-Path $payloadZipDir "lengrvis-payload.zip"
$localAppData = Join-Path $workspacePath "O'Neil\AppData\Local"
$target = Join-Path $localAppData "Lengrvis"
New-Item -ItemType Directory -Path $payloadSource, $payloadZipDir, $localAppData -Force | Out-Null
Set-Content -LiteralPath (Join-Path $payloadSource "marker.txt") -Value "apostrophe-safe" -Encoding ASCII
Compress-Archive -Path (Join-Path $payloadSource "*") -DestinationPath $payloadZip -CompressionLevel Optimal

$oldPayloadZip = $env:LENGRVIS_SFX_PAYLOAD_ZIP
$oldTarget = $env:LENGRVIS_SFX_TARGET
try {
    $env:LENGRVIS_SFX_PAYLOAD_ZIP = $payloadZip
    $env:LENGRVIS_SFX_TARGET = $target
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($launcherPowerShell))
    $powershellCommand = Get-Command powershell.exe -ErrorAction SilentlyContinue
    $powershellPath = if ($powershellCommand) { $powershellCommand.Source } else { "powershell" }
    & $powershellPath -NoProfile -ExecutionPolicy Bypass -EncodedCommand $encoded
    if ($LASTEXITCODE -ne 0) {
        throw "Encoded launcher command failed with exit code $LASTEXITCODE."
    }
}
finally {
    $env:LENGRVIS_SFX_PAYLOAD_ZIP = $oldPayloadZip
    $env:LENGRVIS_SFX_TARGET = $oldTarget
}

$expandedMarker = Join-Path $target "marker.txt"
Assert-Smoke (Test-Path -LiteralPath $expandedMarker -PathType Leaf) "Payload was not expanded into apostrophe-containing LOCALAPPDATA target."
Assert-Smoke ((Get-Content -LiteralPath $expandedMarker -Raw).Trim() -eq "apostrophe-safe") "Expanded payload marker content did not match."

Write-Host "[ok] legacy self-extractor launcher handles apostrophes through env vars and EncodedCommand"
