param(
    [string]$Workspace = ".tmp\verify-packaging-ollama-smoke"
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Add-Type -AssemblyName System.IO.Compression.FileSystem

$DesktopPackage = Get-Content -LiteralPath (Join-Path $Root "desktop\package.json") -Raw | ConvertFrom-Json
$SelfExtractingName = "Lengrvis-$($DesktopPackage.version)-x64-self-extracting.exe"

function Resolve-SmokePath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Get-DirectorySummary {
    param([string]$Path)
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

function New-HealthSmokeExecutable([string]$Path) {
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

function New-SmokeBackendCapabilityManifest([string]$Path) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    $manifest = [ordered]@{
        schema = "lengrvis-backend-capabilities/v1"
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        python = "smoke"
        platform = "win32"
        capabilities = [ordered]@{
            docling = $false
            unstructured = $false
            paddleocr = $false
            pytesseract = $false
            playwright = $false
            pywhispercpp = $false
        }
    }
    Set-Content -LiteralPath $Path -Value ($manifest | ConvertTo-Json -Depth 4) -Encoding ASCII
}

function New-SmokePackage {
    param(
        [string]$RootPath,
        [switch]$IncludeOllama,
        [switch]$UseHealthBackend
    )
    $dist = Join-Path $RootPath "dist"
    $portable = Join-Path $dist "Lengrvis-win-portable"
    $resources = Join-Path $portable "resources"
    if ($UseHealthBackend) {
        New-HealthSmokeExecutable (Join-Path $dist "backend.exe")
    }
    else {
        New-RunnableSmokeExecutable (Join-Path $dist "backend.exe")
    }
    New-RunnableSmokeExecutable (Join-Path $portable "Lengrvis.exe")
    if ($UseHealthBackend) {
        New-HealthSmokeExecutable (Join-Path $resources "backend\backend.exe")
    }
    else {
        New-RunnableSmokeExecutable (Join-Path $resources "backend\backend.exe")
    }
    New-Item -ItemType Directory -Path (Join-Path $resources "app\dist") -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $resources "app\package.json") -Value '{"name":"lengrvis-smoke"}' -Encoding ASCII
    New-SmokeBackendCapabilityManifest (Join-Path $dist "backend-capabilities.json")
    New-SmokeBackendCapabilityManifest (Join-Path $resources "backend\backend-capabilities.json")
    New-SmokeSelfExtractingExecutable (Join-Path $dist $SelfExtractingName)

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
            runtime = [ordered]@{
                summary = Get-DirectorySummary -Path $ollamaDir
            }
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
    return [ordered]@{
        dist = $dist
        portable = $portable
        zip = $zipPath
        selfExtracting = Join-Path $dist $SelfExtractingName
    }
}

function Invoke-VerifyPackaging {
    param(
        [object]$Package,
        [switch]$RequireBundledOllama,
        [switch]$RunExecutableSmoke
    )
    $args = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "$PSScriptRoot\verify_packaging.ps1",
        "-DistDir",
        $Package.dist,
        "-PortableDir",
        $Package.portable,
        "-PortableZip",
        $Package.zip,
        "-SelfExtractingExe",
        $Package.selfExtracting
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
        $script:LastVerifyPackagingExitCode = $LASTEXITCODE
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

$withOllama = New-SmokePackage -RootPath (Join-Path $workspacePath "with-ollama") -IncludeOllama
$withoutOllama = New-SmokePackage -RootPath (Join-Path $workspacePath "without-ollama")
$healthFallback = New-SmokePackage -RootPath (Join-Path $workspacePath "health-fallback") -IncludeOllama -UseHealthBackend

$output = Invoke-VerifyPackaging -Package $withOllama -RequireBundledOllama
if ($script:LastVerifyPackagingExitCode -ne 0) {
    throw "Expected bundled Ollama package verification to pass:`n$output"
}
Write-Host "[ok] package with bundled Ollama passes"

$output = Invoke-VerifyPackaging -Package $withOllama
if ($script:LastVerifyPackagingExitCode -eq 0) {
    throw "Expected bundled Ollama package verification to fail for the default release package."
}
$text = $output | Out-String
if ($text -notmatch "must not be present in the default release package" -or $text -notmatch "zip directory must not be present in the default release package") {
    throw "Unexpected default bundled Ollama failure output:`n$text"
}
Write-Host "[ok] package with bundled Ollama fails for default release package"

$output = Invoke-VerifyPackaging -Package $withOllama -RequireBundledOllama -RunExecutableSmoke
if ($script:LastVerifyPackagingExitCode -ne 0) {
    throw "Expected bundled Ollama package verification with runnable smoke to pass:`n$output"
}
Write-Host "[ok] package with bundled Ollama passes runnable executable smoke"

$output = Invoke-VerifyPackaging -Package $healthFallback -RequireBundledOllama -RunExecutableSmoke
if ($script:LastVerifyPackagingExitCode -ne 0) {
    throw "Expected bundled Ollama package verification to pass runnable /health fallback:`n$output"
}
Write-Host "[ok] package runnable smoke falls back to isolated backend health probe"

$output = Invoke-VerifyPackaging -Package $withoutOllama -RequireBundledOllama
if ($script:LastVerifyPackagingExitCode -eq 0) {
    throw "Expected package without bundled Ollama to fail."
}
$text = $output | Out-String
if ($text -notmatch "portable Ollama runtime missing") {
    throw "Unexpected missing Ollama failure output:`n$text"
}
Write-Host "[ok] package without bundled Ollama fails when required"

$output = Invoke-VerifyPackaging -Package $withoutOllama
if ($script:LastVerifyPackagingExitCode -ne 0) {
    throw "Expected package without bundled Ollama to pass without -RequireBundledOllama:`n$output"
}
Write-Host "[ok] package without bundled Ollama remains valid when not required"

[System.IO.File]::WriteAllBytes($withoutOllama.selfExtracting, [byte[]](0x4d,0x5a,0x90,0x00))
$output = Invoke-VerifyPackaging -Package $withoutOllama
if ($script:LastVerifyPackagingExitCode -eq 0) {
    throw "Expected tiny fake self-extracting exe to fail packaging verification."
}
$text = $output | Out-String
if ($text -notmatch "self-extracting executable is too small") {
    throw "Unexpected tiny SFX failure output:`n$text"
}
Write-Host "[ok] tiny fake self-extracting exe is rejected"

Write-Host ""
Write-Host "Bundled Ollama packaging smoke passed." -ForegroundColor Green
