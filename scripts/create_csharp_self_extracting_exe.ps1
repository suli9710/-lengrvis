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

function Resolve-CanonicalPath {
    param([string]$Path)
    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-SameOrNestedPath {
    param(
        [string]$Parent,
        [string]$Candidate
    )
    $parentCanonical = Resolve-CanonicalPath -Path $Parent
    $candidateCanonical = Resolve-CanonicalPath -Path $Candidate
    return $candidateCanonical.Equals($parentCanonical, [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidateCanonical.StartsWith("$parentCanonical\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $candidateCanonical.StartsWith("$parentCanonical/", [System.StringComparison]::OrdinalIgnoreCase)
}

function Resolve-ReleaseOutputPath {
    param(
        [string]$Path,
        [string]$Label
    )
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "$Label must not be empty."
    }
    $resolved = Resolve-CanonicalPath -Path (Resolve-ProjectPath $Path)
    $allowedRoots = @(
        (Join-Path $Root "dist"),
        (Join-Path $Root "release")
    )
    foreach ($allowedRoot in $allowedRoots) {
        if (Test-SameOrNestedPath -Parent $allowedRoot -Candidate $resolved) {
            return $resolved
        }
    }
    throw "$Label must stay under repository dist or release directories. Got: $resolved"
}

$ZipPath = Resolve-ProjectPath $PortableZip
$OutputPath = Resolve-ReleaseOutputPath -Path $OutputExe -Label "OutputExe"
$BuildDir = Join-Path $Root "build\csharp-sfx"
$SourcePath = Join-Path $BuildDir "LengrvisSfx.cs"
$PayloadPath = Join-Path $BuildDir "payload.zip"
$Csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"

if (-not (Test-Path $ZipPath)) {
    throw "Portable zip was not found at $ZipPath. Run scripts\build_portable.ps1 and compress it first."
}

if (-not (Test-Path $Csc)) {
    throw "C# compiler was not found at $Csc"
}

if (Test-Path $BuildDir) {
    $Resolved = Resolve-Path -LiteralPath $BuildDir
    $RootPrefix = $Root.TrimEnd('\') + '\'
    if (-not $Resolved.Path.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove build dir outside project root: $($Resolved.Path)"
    }
    Remove-Item -LiteralPath $Resolved.Path -Recurse -Force
}

New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null
Copy-Item -LiteralPath $ZipPath -Destination $PayloadPath -Force

@'
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;

internal static class Program
{
    [STAThread]
    private static int Main()
    {
        try
        {
            string target = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Lengrvis"
            );
            Directory.CreateDirectory(target);
            Directory.CreateDirectory(Path.Combine(target, ".lengrvis_data"));
            Directory.CreateDirectory(Path.Combine(target, "logs"));
            string appTarget = Path.Combine(target, "app");
            if (Directory.Exists(appTarget))
            {
                Directory.Delete(appTarget, true);
            }
            Directory.CreateDirectory(appTarget);

            string tempZip = Path.Combine(Path.GetTempPath(), "lengrvis-payload-" + Guid.NewGuid().ToString("N") + ".zip");
            using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
            {
                if (resource == null)
                {
                    throw new InvalidOperationException("Embedded Lengrvis payload was not found.");
                }
                using (FileStream file = File.Create(tempZip))
                {
                    resource.CopyTo(file);
                }
            }

            ZipFile.ExtractToDirectory(tempZip, appTarget);
            File.Delete(tempZip);

            string exe = Path.Combine(appTarget, "Lengrvis.exe");
            if (!File.Exists(exe))
            {
                throw new FileNotFoundException("Lengrvis.exe was not extracted.", exe);
            }

            ProcessStartInfo startInfo = new ProcessStartInfo
            {
                FileName = exe,
                WorkingDirectory = appTarget,
                UseShellExecute = false
            };
            startInfo.EnvironmentVariables["LENGRVIS_CONFIG_DIR"] = target;
            startInfo.EnvironmentVariables["LENGRVIS_DATA_DIR"] = Path.Combine(target, ".lengrvis_data");
            Process.Start(startInfo);
            return 0;
        }
        catch (Exception ex)
        {
            File.WriteAllText(
                Path.Combine(Path.GetTempPath(), "lengrvis-sfx-error.txt"),
                ex.ToString()
            );
            return 1;
        }
    }
}
'@ | Set-Content -LiteralPath $SourcePath -Encoding UTF8

if (Test-Path $OutputPath) {
    Remove-Item -LiteralPath $OutputPath -Force
}

$ResourceArg = "/resource:$PayloadPath,payload.zip"
& $Csc /nologo /target:winexe /platform:x64 /optimize+ /out:$OutputPath $ResourceArg /reference:System.IO.Compression.FileSystem.dll $SourcePath
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not (Test-Path $OutputPath)) {
    throw "Self-extracting exe was not created at $OutputPath"
}

Write-Host "Self-extracting exe created at $OutputPath"
