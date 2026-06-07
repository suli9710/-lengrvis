param(
    [string]$Workspace = ".tmp\build-all-verify-gate-smoke"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Resolve-SmokePath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function New-SmokeExecutable([string]$Path) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [byte[]]$bytes = 0x4d,0x5a,0x90,0x00
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function New-BackendHealthSmokeExecutable([string]$Path) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Force
    }
    $className = "PackagingSmoke_" + [guid]::NewGuid().ToString("N")
    $source = @"
public static class $className {
    public static int Main(string[] args) {
        if (args.Length > 0) {
            System.Console.Error.WriteLine("health fixture expects no command-line arguments; received: " + string.Join(" ", args));
            return 2;
        }
        string rawPort = System.Environment.GetEnvironmentVariable("LENGRVIS_BACKEND_PORT");
        int port;
        if (string.IsNullOrWhiteSpace(rawPort)) {
            port = 8000;
        }
        else if (!int.TryParse(rawPort, out port) || port < 1 || port > 65535) {
            System.Console.Error.WriteLine("invalid LENGRVIS_BACKEND_PORT for health fixture: '" + rawPort + "'");
            return 64;
        }

        const string host = "127.0.0.1";
        System.Net.Sockets.TcpListener listener = null;
        try {
            listener = new System.Net.Sockets.TcpListener(System.Net.IPAddress.Parse(host), port);
            listener.Server.ExclusiveAddressUse = true;
            listener.Start();
            System.Console.Out.WriteLine("health fixture listening on http://" + host + ":" + port + "/health");
            System.Console.Out.Flush();

            while (true) {
                try {
                    using (System.Net.Sockets.TcpClient client = listener.AcceptTcpClient()) {
                        client.ReceiveTimeout = 2000;
                        client.SendTimeout = 2000;
                        ServeClient(client);
                    }
                }
                catch (System.IO.IOException ex) {
                    System.Console.Error.WriteLine("health fixture closed stalled client: " + ex.Message);
                    System.Console.Error.Flush();
                }
            }
        }
        catch (System.Exception ex) {
            System.Console.Error.WriteLine("health fixture failed on " + host + ":" + port + ": " + ex.GetType().Name + ": " + ex.Message);
            return 65;
        }
        finally {
            if (listener != null) {
                listener.Stop();
            }
        }
    }

    private static void ServeClient(System.Net.Sockets.TcpClient client) {
        using (System.Net.Sockets.NetworkStream stream = client.GetStream()) {
            stream.ReadTimeout = 2000;
            stream.WriteTimeout = 2000;
            string firstLine = ReadRequestLine(stream);
            string[] parts = string.IsNullOrWhiteSpace(firstLine)
                ? new string[0]
                : firstLine.Split(new char[] { ' ' }, 3, System.StringSplitOptions.RemoveEmptyEntries);
            string method = parts.Length > 0 ? parts[0] : "";
            string path = parts.Length > 1 ? parts[1] : "";
            bool isHealth = string.Equals(method, "GET", System.StringComparison.OrdinalIgnoreCase)
                && (string.Equals(path, "/health", System.StringComparison.OrdinalIgnoreCase)
                    || path.StartsWith("/health?", System.StringComparison.OrdinalIgnoreCase));
            int statusCode = isHealth ? 200 : 404;
            string statusText = isHealth ? "OK" : "Not Found";
            string bodyText = isHealth ? "{\"status\":\"ok\"}" : "{\"error\":\"not found\"}";
            byte[] body = System.Text.Encoding.UTF8.GetBytes(bodyText);
            string header = "HTTP/1.1 " + statusCode + " " + statusText + "\r\nContent-Type: application/json\r\nContent-Length: " + body.Length + "\r\nConnection: close\r\n\r\n";
            byte[] headerBytes = System.Text.Encoding.ASCII.GetBytes(header);
            stream.Write(headerBytes, 0, headerBytes.Length);
            stream.Write(body, 0, body.Length);
            System.Console.Out.WriteLine("health fixture " + (firstLine.Length == 0 ? "<empty request>" : firstLine) + " -> " + statusCode);
            System.Console.Out.Flush();
        }
    }

    private static string ReadRequestLine(System.Net.Sockets.NetworkStream stream) {
        byte[] buffer = new byte[2048];
        int count = 0;
        while (count < buffer.Length) {
            int read = stream.Read(buffer, count, buffer.Length - count);
            if (read <= 0) {
                break;
            }
            count += read;
            for (int index = 0; index < count; index++) {
                if (buffer[index] == 10) {
                    int length = index;
                    if (length > 0 && buffer[length - 1] == 13) {
                        length--;
                    }
                    return System.Text.Encoding.ASCII.GetString(buffer, 0, length);
                }
            }
        }
        if (count == 0) {
            return "";
        }
        string request = System.Text.Encoding.ASCII.GetString(buffer, 0, count);
        int lineEnd = request.IndexOfAny(new char[] { '\r', '\n' });
        return lineEnd >= 0 ? request.Substring(0, lineEnd) : request;
    }
}
"@
    Add-Type -TypeDefinition $source -OutputAssembly $Path -OutputType ConsoleApplication
}

function New-RunnableSmokeExecutable([string]$Path) {
    New-BackendHealthSmokeExecutable -Path $Path
}

function New-SmokeSelfExtractingExecutable([string]$Path) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [byte[]]$bytes = [byte[]]::new(131072)
    $peOffset = 0x80
    $bytes[0] = 0x4d
    $bytes[1] = 0x5a
    [System.BitConverter]::GetBytes([uint32]$peOffset).CopyTo($bytes, 0x3c)
    $bytes[$peOffset] = 0x50
    $bytes[$peOffset + 1] = 0x45
    [System.BitConverter]::GetBytes([uint16]0x8664).CopyTo($bytes, $peOffset + 4)
    [System.BitConverter]::GetBytes([uint16]3).CopyTo($bytes, $peOffset + 6)
    [System.BitConverter]::GetBytes([uint16]0x00f0).CopyTo($bytes, $peOffset + 0x14)
    [System.BitConverter]::GetBytes([uint16]0x020b).CopyTo($bytes, $peOffset + 0x18)
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function Get-DirectorySummary([string]$Path) {
    $rootPath = (Resolve-Path -LiteralPath $Path).Path
    $files = Get-ChildItem -LiteralPath $Path -File -Recurse -Force | Sort-Object FullName
    $hash = [System.Security.Cryptography.SHA256]::Create()
    try {
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($rootPath.Length).TrimStart('\', '/')
            $nameBytes = [System.Text.Encoding]::UTF8.GetBytes($relative.ToLowerInvariant())
            $hash.TransformBlock($nameBytes, 0, $nameBytes.Length, $null, 0) | Out-Null
            $content = [System.IO.File]::ReadAllBytes($file.FullName)
            $hash.TransformBlock($content, 0, $content.Length, $null, 0) | Out-Null
        }
        $hash.TransformFinalBlock([byte[]]::new(0), 0, 0) | Out-Null
        $digest = -join ($hash.Hash | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $hash.Dispose()
    }
    return [ordered]@{
        present = $true
        files = @($files).Count
        bytes = [int64](($files | Measure-Object -Property Length -Sum).Sum)
        sha256 = $digest
    }
}

function New-SmokePackage {
    param(
        [string]$PackageRoot,
        [switch]$IncludeOllama
    )
    $dist = Join-Path $PackageRoot "dist"
    $portable = Join-Path $dist "Lengrvis-win-portable"
    $resources = Join-Path $portable "resources"
    New-RunnableSmokeExecutable (Join-Path $dist "backend.exe")
    New-RunnableSmokeExecutable (Join-Path $portable "Lengrvis.exe")
    New-RunnableSmokeExecutable (Join-Path $resources "backend\backend.exe")
    New-Item -ItemType Directory -Path (Join-Path $resources "app\dist") -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $resources "app\package.json") -Value '{"name":"lengrvis-smoke"}' -Encoding ASCII
    New-SmokeSelfExtractingExecutable (Join-Path $dist "Lengrvis-0.1.0-x64-self-extracting.exe")

    if ($IncludeOllama) {
        $ollamaDir = Join-Path $resources "ollama"
        $modelsDir = Join-Path $resources "ollama-models"
        $modelManifest = Join-Path $modelsDir "manifests\registry.ollama.ai\library\qwen2.5\3b"
        New-SmokeExecutable (Join-Path $ollamaDir "ollama.exe")
        New-Item -ItemType Directory -Path (Split-Path -Parent $modelManifest) -Force | Out-Null
        Set-Content -LiteralPath $modelManifest -Value '{}' -Encoding ASCII
        $manifest = [ordered]@{
            schema = 1
            model = "qwen2.5:3b"
            accepted_licenses = $true
            runtime = [ordered]@{ summary = Get-DirectorySummary -Path $ollamaDir }
            models = [ordered]@{
                model_manifest = "manifests/registry.ollama.ai/library/qwen2.5/3b"
                summary = Get-DirectorySummary -Path $modelsDir
            }
        }
        $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $resources "ollama-bundle-manifest.json") -Encoding UTF8
    }

    $zipPath = Join-Path $dist "Lengrvis-win-portable.zip"
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($portable, $zipPath)
}

function Invoke-BuildVerify {
    param(
        [string]$ScriptName,
        [string]$PackageRoot,
        [switch]$RequireBundledOllama,
        [switch]$RunExecutableSmoke,
        [int]$SmokeTimeoutSeconds = 10
    )
    $dist = Join-Path $PackageRoot "dist"
    $portable = Join-Path $dist "Lengrvis-win-portable"
    $zip = Join-Path $dist "Lengrvis-win-portable.zip"
    $selfExtracting = Join-Path $dist "Lengrvis-0.1.0-x64-self-extracting.exe"
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "$PSScriptRoot\$ScriptName",
        "-VerifyOnly",
        "-DistDir",
        $dist,
        "-PortableDir",
        $portable,
        "-PortableZip",
        $zip,
        "-SelfExtractingExe",
        $selfExtracting
    )
    if ($RequireBundledOllama) {
        $args += "-RequireBundledOllama"
    }
    if ($RunExecutableSmoke) {
        $args += @("-RunExecutableSmoke", "-SmokeTimeoutSeconds", $SmokeTimeoutSeconds)
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell @args 2>&1
        $script:LastBuildAllVerifyExitCode = $LASTEXITCODE
        return $output
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

function Assert-RunnableSmokeOutput {
    param(
        [object]$Output,
        [string]$Label
    )
    $text = $Output | Out-String
    if ($text -notmatch "backend executable runnable smoke served" -or $text -notmatch "portable backend executable runnable smoke served") {
        throw "Expected $Label output to include runnable executable smoke results:`n$text"
    }
}

function Invoke-DirectPackagingVerify {
    param(
        [string]$PackageRoot,
        [switch]$RequireBundledOllama,
        [switch]$RunExecutableSmoke
    )
    $dist = Join-Path $PackageRoot "dist"
    $portable = Join-Path $dist "Lengrvis-win-portable"
    $zip = Join-Path $dist "Lengrvis-win-portable.zip"
    $selfExtracting = Join-Path $dist "Lengrvis-0.1.0-x64-self-extracting.exe"
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "$PSScriptRoot\verify_packaging.ps1",
        "-DistDir",
        $dist,
        "-PortableDir",
        $portable,
        "-PortableZip",
        $zip,
        "-SelfExtractingExe",
        $selfExtracting
    )
    if ($RequireBundledOllama) {
        $args += "-RequireBundledOllama"
    }
    if ($RunExecutableSmoke) {
        $args += @("-RunExecutableSmoke", "-SmokeTimeoutSeconds", "10")
    }
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = & powershell @args 2>&1
        $script:LastDirectVerifyExitCode = $LASTEXITCODE
        return $output
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}

$workspacePath = Resolve-SmokePath $Workspace
if (Test-Path -LiteralPath $workspacePath) {
    Remove-Item -LiteralPath $workspacePath -Recurse -Force
}
New-Item -ItemType Directory -Path $workspacePath -Force | Out-Null

$withOllama = Join-Path $workspacePath "with-ollama"
$withoutOllama = Join-Path $workspacePath "without-ollama"
New-SmokePackage -PackageRoot $withOllama -IncludeOllama
New-SmokePackage -PackageRoot $withoutOllama

$output = Invoke-BuildVerify -ScriptName "build_all.ps1" -PackageRoot $withOllama -RequireBundledOllama
if ($script:LastBuildAllVerifyExitCode -ne 0) {
    throw "Expected build_all -VerifyOnly -RequireBundledOllama to pass with bundled resources:`n$output"
}
Write-Host "[ok] build_all verify gate passes with bundled Ollama"

$output = Invoke-BuildVerify -ScriptName "build_all.ps1" -PackageRoot $withoutOllama -RequireBundledOllama
if ($script:LastBuildAllVerifyExitCode -eq 0) {
    throw "Expected build_all -VerifyOnly -RequireBundledOllama to fail without bundled resources."
}
$text = $output | Out-String
if ($text -notmatch "portable Ollama runtime missing") {
    throw "Unexpected build_all verify failure output:`n$text"
}
Write-Host "[ok] build_all verify gate fails without bundled Ollama when required"

$output = Invoke-BuildVerify -ScriptName "build_all.ps1" -PackageRoot $withoutOllama
if ($script:LastBuildAllVerifyExitCode -ne 0) {
    throw "Expected build_all -VerifyOnly to pass without bundled resources when not required:`n$output"
}
Write-Host "[ok] build_all verify gate remains compatible without bundled requirement"

$output = Invoke-BuildVerify -ScriptName "build_all.ps1" -PackageRoot $withOllama -RequireBundledOllama -RunExecutableSmoke
if ($script:LastBuildAllVerifyExitCode -ne 0) {
    throw "Expected build_all -VerifyOnly -RunExecutableSmoke to pass with bundled resources:`n$output"
}
Assert-RunnableSmokeOutput -Output $output -Label "build_all -RunExecutableSmoke"
Write-Host "[ok] build_all verify gate passes runnable executable smoke"

$output = Invoke-DirectPackagingVerify -PackageRoot $withOllama -RequireBundledOllama -RunExecutableSmoke
if ($script:LastDirectVerifyExitCode -ne 0) {
    throw "Expected direct packaging verify to pass runnable smoke with bundled resources:`n$output"
}
Write-Host "[ok] direct packaging verify gate passes runnable executable smoke"

$output = Invoke-BuildVerify -ScriptName "build.ps1" -PackageRoot $withOllama -RequireBundledOllama -RunExecutableSmoke
if ($script:LastBuildAllVerifyExitCode -ne 0) {
    throw "Expected build.ps1 wrapper to pass through -VerifyOnly -RequireBundledOllama -RunExecutableSmoke:`n$output"
}
Assert-RunnableSmokeOutput -Output $output -Label "build.ps1 -RunExecutableSmoke"
Write-Host "[ok] build.ps1 wrapper passes verify gate smoke arguments"

Write-Host ""
Write-Host "build_all verification gate smoke passed." -ForegroundColor Green
