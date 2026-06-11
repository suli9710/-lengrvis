param(
    [ValidateSet("install", "update", "start", "stop", "restart", "query")]
    [string]$Action = "install",
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000,
    [string]$LogLevel = "info",
    [switch]$LanTlsEnabled,
    [string]$LanTlsCertFile = "",
    [string]$LanTlsKeyFile = "",
    [switch]$StartupAuto,
    [switch]$DelayedAutoStart,
    [switch]$SkipHealthCheck,
    [int]$WaitSeconds = 30
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $Root "logs"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-Python {
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }
    throw "Python was not found. Install Python 3.11+ or create .venv first."
}

function Test-Pywin32([string]$Python) {
    & $Python -c "import win32serviceutil, win32service, win32event, servicemanager" *> $null
    return $LASTEXITCODE -eq 0
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Wait-BackendHealth {
    param(
        [string]$HostName,
        [int]$Port,
        [string]$Scheme,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $uri = "${Scheme}://${HostName}:${Port}/health"
    Write-Step "Waiting for backend health: $uri"
    do {
        try {
            $request = @{
                Uri = $uri
                UseBasicParsing = $true
                TimeoutSec = 2
            }
            # Self-signed certs are only expected on loopback; never skip
            # certificate validation when probing a non-loopback host.
            $isLoopbackHost = $HostName -in @("127.0.0.1", "localhost", "::1", "[::1]")
            if ($Scheme -eq "https" -and $isLoopbackHost -and (Get-Command Invoke-WebRequest).Parameters.ContainsKey("SkipCertificateCheck")) {
                $request.SkipCertificateCheck = $true
            }
            $response = Invoke-WebRequest @request
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Host "Backend health check passed."
                return
            }
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)

    throw "Backend health check did not pass within $TimeoutSeconds seconds: $uri"
}

$IsWindowsPlatform = ($PSVersionTable.PSEdition -eq "Desktop") -or $IsWindows
if (-not $IsWindowsPlatform) {
    throw "Lengrvis Windows Service can only be installed or controlled on Windows."
}

Set-Location $Root
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$pythonExe = Find-Python
if (-not (Test-Pywin32 $pythonExe)) {
    throw "pywin32 is required. Install backend dependencies first: $pythonExe -m pip install -r backend\requirements.txt"
}

if ($Action -ne "query" -and -not (Test-Admin)) {
    throw "Administrator privileges are required for Windows Service installation/control. Re-run PowerShell as Administrator."
}

$env:LENGRVIS_BACKEND_HOST = $BackendHost
$env:LENGRVIS_BACKEND_PORT = [string]$BackendPort
$env:LENGRVIS_BACKEND_LOG_LEVEL = $LogLevel
$env:LENGRVIS_CONFIG_DIR = $Root
if ($LanTlsEnabled) {
    $env:LENGRVIS_LAN_TLS_ENABLED = "true"
    if ($LanTlsCertFile) {
        $env:LENGRVIS_LAN_TLS_CERT_FILE = $LanTlsCertFile
    }
    if ($LanTlsKeyFile) {
        $env:LENGRVIS_LAN_TLS_KEY_FILE = $LanTlsKeyFile
    }
}

$serviceArgs = @()
switch ($Action) {
    "install" {
        $serviceArgs += "install"
        if ($DelayedAutoStart) {
            $serviceArgs += @("--startup", "delayed")
        }
        elseif ($StartupAuto) {
            $serviceArgs += @("--startup", "auto")
        }
        $serviceArgs += @(
            "--project-root", $Root.Path,
            "--backend-host", $BackendHost,
            "--backend-port", [string]$BackendPort,
            "--backend-log-level", $LogLevel
        )
        if ($LanTlsEnabled) {
            $serviceArgs += "--lan-tls-enabled"
        }
        if ($LanTlsCertFile) {
            $serviceArgs += @("--lan-tls-cert-file", $LanTlsCertFile)
        }
        if ($LanTlsKeyFile) {
            $serviceArgs += @("--lan-tls-key-file", $LanTlsKeyFile)
        }
    }
    "update" {
        $serviceArgs += "update"
        if ($DelayedAutoStart) {
            $serviceArgs += @("--startup", "delayed")
        }
        elseif ($StartupAuto) {
            $serviceArgs += @("--startup", "auto")
        }
        $serviceArgs += @(
            "--project-root", $Root.Path,
            "--backend-host", $BackendHost,
            "--backend-port", [string]$BackendPort,
            "--backend-log-level", $LogLevel
        )
        if ($LanTlsEnabled) {
            $serviceArgs += "--lan-tls-enabled"
        }
        if ($LanTlsCertFile) {
            $serviceArgs += @("--lan-tls-cert-file", $LanTlsCertFile)
        }
        if ($LanTlsKeyFile) {
            $serviceArgs += @("--lan-tls-key-file", $LanTlsKeyFile)
        }
    }
    "query" {
        $serviceArgs += "query"
    }
    default {
        $serviceArgs += @($Action, "--wait", [string]$WaitSeconds)
    }
}

Write-Step "Running service action: $($serviceArgs -join ' ')"
& $pythonExe -m backend.service_wrapper @serviceArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (($Action -eq "start" -or $Action -eq "restart") -and -not $SkipHealthCheck) {
    $healthScheme = if ($LanTlsEnabled) { "https" } else { "http" }
    Wait-BackendHealth -HostName $BackendHost -Port $BackendPort -Scheme $healthScheme -TimeoutSeconds $WaitSeconds
}

if ($Action -eq "install") {
    Write-Host "Service installed. Start it with:"
    Write-Host "  scripts\install_service.ps1 -Action start"
}
