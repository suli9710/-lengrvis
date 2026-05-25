param(
    [string]$PortableDir = "dist\Mavris-win-portable",
    [string]$OutputExe = "dist\Mavris-0.1.0-x64-self-extracting.exe"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$PortablePath = Join-Path $Root $PortableDir
$OutputPath = Join-Path $Root $OutputExe
$BuildDir = Join-Path $Root "build\self-extracting"
$PayloadZip = Join-Path $BuildDir "mavris-payload.zip"
$LauncherCmd = Join-Path $BuildDir "launch.cmd"
$SedPath = Join-Path $BuildDir "mavris-sfx.sed"

if (-not (Test-Path (Join-Path $PortablePath "Mavris.exe"))) {
    throw "Portable Mavris.exe was not found. Run scripts\build_portable.ps1 first."
}

if (Test-Path $BuildDir) {
    $ResolvedBuild = Resolve-Path -LiteralPath $BuildDir
    if ($ResolvedBuild.Path -notlike "$Root*") {
        throw "Refusing to remove build dir outside project root: $($ResolvedBuild.Path)"
    }
    Remove-Item -LiteralPath $ResolvedBuild.Path -Recurse -Force
}

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
if (Test-Path $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

Compress-Archive -Path (Join-Path $PortablePath "*") -DestinationPath $PayloadZip -CompressionLevel Optimal

@'
@echo off
setlocal
set "TARGET=%LOCALAPPDATA%\Mavris"
if not exist "%TARGET%" mkdir "%TARGET%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%~dp0mavris-payload.zip' -DestinationPath '%TARGET%' -Force"
start "" "%TARGET%\Mavris.exe"
endlocal
'@ | Set-Content -LiteralPath $LauncherCmd -Encoding ASCII

@"
[Version]
Class=IEXPRESS
SEDVersion=3
[Options]
PackagePurpose=InstallApp
ShowInstallProgramWindow=0
HideExtractAnimation=1
UseLongFileName=1
InsideCompressed=0
CAB_FixedSize=0
CAB_ResvCodeSigning=0
RebootMode=N
InstallPrompt=
DisplayLicense=
FinishMessage=
TargetName=$OutputPath
FriendlyName=Mavris
AppLaunched=launch.cmd
PostInstallCmd=<None>
AdminQuietInstCmd=launch.cmd
UserQuietInstCmd=launch.cmd
SourceFiles=SourceFiles
[Strings]
FILE0="launch.cmd"
FILE1="mavris-payload.zip"
[SourceFiles]
SourceFiles0=$BuildDir
[SourceFiles0]
%FILE0%=
%FILE1%=
"@ | Set-Content -LiteralPath $SedPath -Encoding ASCII

iexpress.exe /N /Q $SedPath
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not (Test-Path $OutputPath)) {
    throw "Self-extracting exe was not created at $OutputPath"
}

Write-Host "Self-extracting exe created at $OutputPath"
