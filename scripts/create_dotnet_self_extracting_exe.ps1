param(
    [string]$PortableZip = "dist\Lengrvis-win-portable.zip",
    [string]$OutputExe = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if ([string]::IsNullOrWhiteSpace($OutputExe)) {
    $desktopPackage = Get-Content -LiteralPath (Join-Path $Root "desktop\package.json") -Raw | ConvertFrom-Json
    $OutputExe = "dist\Lengrvis-$($desktopPackage.version)-x64-self-extracting.exe"
}

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

$ZipPath = Resolve-ProjectPath $PortableZip
$OutputPath = Resolve-ProjectPath $OutputExe
$BuildDir = Join-Path $Root "build\dotnet-sfx"
$ProjectDir = Join-Path $BuildDir "LengrvisSfx"

if (-not (Test-Path $ZipPath)) {
    throw "Portable zip was not found at $ZipPath. Run scripts\build_portable.ps1 and compress it first."
}

if (Test-Path $BuildDir) {
    $Resolved = Resolve-Path -LiteralPath $BuildDir
    if ($Resolved.Path -notlike "$Root*") {
        throw "Refusing to remove build dir outside project root: $($Resolved.Path)"
    }
    Remove-Item -LiteralPath $Resolved.Path -Recurse -Force
}

New-Item -ItemType Directory -Path $ProjectDir -Force | Out-Null
Copy-Item -LiteralPath $ZipPath -Destination (Join-Path $ProjectDir "payload.zip") -Force

@'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <OutputType>WinExe</OutputType>
    <TargetFramework>net8.0-windows</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <PublishSingleFile>true</PublishSingleFile>
    <SelfContained>true</SelfContained>
    <RuntimeIdentifier>win-x64</RuntimeIdentifier>
    <EnableCompressionInSingleFile>true</EnableCompressionInSingleFile>
    <AssemblyName>Lengrvis</AssemblyName>
    <Version>0.1.0</Version>
  </PropertyGroup>
  <ItemGroup>
    <EmbeddedResource Include="payload.zip" LogicalName="payload.zip" />
  </ItemGroup>
</Project>
'@ | Set-Content -LiteralPath (Join-Path $ProjectDir "LengrvisSfx.csproj") -Encoding UTF8

@'
using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;

var target = Path.Combine(
    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
    "Lengrvis"
);
Directory.CreateDirectory(target);

var tempZip = Path.Combine(Path.GetTempPath(), $"lengrvis-payload-{Guid.NewGuid():N}.zip");
await using (var resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
{
    if (resource is null)
    {
        throw new InvalidOperationException("Embedded Lengrvis payload was not found.");
    }
    await using var file = File.Create(tempZip);
    await resource.CopyToAsync(file);
}

ZipFile.ExtractToDirectory(tempZip, target, overwriteFiles: true);
File.Delete(tempZip);

var exe = Path.Combine(target, "Lengrvis.exe");
if (!File.Exists(exe))
{
    throw new FileNotFoundException("Lengrvis.exe was not extracted.", exe);
}

Process.Start(new ProcessStartInfo
{
    FileName = exe,
    WorkingDirectory = target,
    UseShellExecute = true
});
'@ | Set-Content -LiteralPath (Join-Path $ProjectDir "Program.cs") -Encoding UTF8

dotnet publish $ProjectDir -c Release -r win-x64 --self-contained true -p:PublishSingleFile=true -p:EnableCompressionInSingleFile=true
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$PublishedExe = Join-Path $ProjectDir "bin\Release\net8.0-windows\win-x64\publish\Lengrvis.exe"
if (-not (Test-Path $PublishedExe)) {
    throw "Published launcher was not created at $PublishedExe"
}

if (Test-Path $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}
Copy-Item -LiteralPath $PublishedExe -Destination $OutputPath -Force
Write-Host "Self-extracting exe created at $OutputPath"
