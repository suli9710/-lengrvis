param(
    [string]$PortableZip = "dist\Mavris-win-portable.zip",
    [string]$OutputExe = "dist\Mavris-0.1.0-x64-self-extracting.exe"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$ZipPath = Join-Path $Root $PortableZip
$OutputPath = Join-Path $Root $OutputExe
$BuildDir = Join-Path $Root "build\csharp-sfx"
$SourcePath = Join-Path $BuildDir "MavrisSfx.cs"
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
    if ($Resolved.Path -notlike "$Root*") {
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
                "Mavris"
            );
            if (Directory.Exists(target))
            {
                Directory.Delete(target, true);
            }
            Directory.CreateDirectory(target);

            string tempZip = Path.Combine(Path.GetTempPath(), "mavris-payload-" + Guid.NewGuid().ToString("N") + ".zip");
            using (Stream resource = Assembly.GetExecutingAssembly().GetManifestResourceStream("payload.zip"))
            {
                if (resource == null)
                {
                    throw new InvalidOperationException("Embedded Mavris payload was not found.");
                }
                using (FileStream file = File.Create(tempZip))
                {
                    resource.CopyTo(file);
                }
            }

            ZipFile.ExtractToDirectory(tempZip, target);
            File.Delete(tempZip);

            string exe = Path.Combine(target, "Mavris.exe");
            if (!File.Exists(exe))
            {
                throw new FileNotFoundException("Mavris.exe was not extracted.", exe);
            }

            Process.Start(new ProcessStartInfo
            {
                FileName = exe,
                WorkingDirectory = target,
                UseShellExecute = true
            });
            return 0;
        }
        catch (Exception ex)
        {
            File.WriteAllText(
                Path.Combine(Path.GetTempPath(), "mavris-sfx-error.txt"),
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

& $Csc /nologo /target:winexe /platform:x64 /optimize+ /out:$OutputPath /resource:$PayloadPath,payload.zip /reference:System.IO.Compression.FileSystem.dll $SourcePath
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not (Test-Path $OutputPath)) {
    throw "Self-extracting exe was not created at $OutputPath"
}

Write-Host "Self-extracting exe created at $OutputPath"
