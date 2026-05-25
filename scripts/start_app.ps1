param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$SkipInstall,
    [switch]$CheckOnly,
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$BackendUrl = "http://$BackendHost`:$BackendPort"
$FrontendUrl = "http://127.0.0.1`:$FrontendPort"
$LogDir = Join-Path $Root "logs"
$BackendStdoutLog = Join-Path $LogDir "backend.out.log"
$BackendStderrLog = Join-Path $LogDir "backend.err.log"
$DesktopLog = Join-Path $LogDir "desktop.log"
$FrontendStdoutLog = Join-Path $LogDir "frontend.out.log"
$FrontendStderrLog = Join-Path $LogDir "frontend.err.log"
$startedBackend = $null
$startedFrontend = $null
$leaveProcessesRunning = $false

function Write-Step([string]$Message) {
    Write-Host ""
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
    throw "Python was not found. Install Python 3.12+ or create .venv first."
}

function Find-Npm {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npm) {
        return $npm.Source
    }
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($npm) {
        return $npm.Source
    }
    throw "npm was not found. Install Node.js 20+ first."
}

function Test-Health {
    try {
        $response = Invoke-WebRequest -Uri "$BackendUrl/api/health" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
    }
    catch {
        return $false
    }
}

function Get-ListenProcess([int]$Port) {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $connection) {
        return $null
    }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
}

function Test-WorkspaceProcess([string]$CommandLine) {
    return $CommandLine -and $CommandLine.ToLowerInvariant().Contains($Root.Path.ToLowerInvariant())
}

function Test-PackagedMavrisBackend([string]$CommandLine) {
    $lower = if ($CommandLine) { $CommandLine.ToLowerInvariant() } else { "" }
    return $lower.Contains("\mavris\resources\backend\backend.exe")
}

function Test-UvicornMavrisBackend([string]$CommandLine) {
    $lower = if ($CommandLine) { $CommandLine.ToLowerInvariant() } else { "" }
    return $lower.Contains("uvicorn") -and $lower.Contains("backend.main:app")
}

function Stop-WorkspaceProcessOnPort([int]$Port, [string]$Purpose) {
    $process = Get-ListenProcess $Port
    if (-not $process) {
        return
    }

    $commandLine = [string]$process.CommandLine
    if (Test-WorkspaceProcess $commandLine) {
        Write-Step "Stopping stale $Purpose process on port $Port"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        Start-Sleep -Milliseconds 500
        return
    }

    throw "Port $Port is already used by another process: $commandLine"
}

function Ensure-NodeDependencies([string]$Npm) {
    if ($SkipInstall) {
        return
    }

    if (-not (Test-Path (Join-Path $Root "desktop\node_modules"))) {
        Write-Step "Installing desktop dependencies"
        & $Npm --prefix (Join-Path $Root "desktop") install
        if ($LASTEXITCODE -ne 0) {
            throw "npm install failed."
        }
    }
}

function Ensure-PythonDependencies([string]$Python) {
    if ($SkipInstall) {
        return
    }

    $dependenciesAvailable = $false
    try {
        & $Python -c "import fastapi, jwt, pydantic, uvicorn" *> $null
        $dependenciesAvailable = $LASTEXITCODE -eq 0
    }
    catch {
        $dependenciesAvailable = $false
    }

    if ($dependenciesAvailable) {
        return
    }

    Write-Step "Installing backend dependencies"
    & $Python -m pip install -r (Join-Path $Root "backend\requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed."
    }
}

function Start-Backend([string]$Python) {
    $existing = Get-ListenProcess $BackendPort
    if ($existing) {
        $commandLine = [string]$existing.CommandLine
        if (Test-PackagedMavrisBackend $commandLine) {
            Write-Step "Stopping installed Mavris backend so this workspace version can run"
            Stop-Process -Id $existing.ProcessId -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 700
        }
        elseif ((Test-WorkspaceProcess $commandLine) -or (Test-UvicornMavrisBackend $commandLine)) {
            if (Test-Health) {
                Write-Step "Mavris backend already running at $BackendUrl"
                return $null
            }
            Stop-Process -Id $existing.ProcessId -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 500
        }
        else {
            throw "Backend port $BackendPort is already used by another process: $commandLine"
        }
    }

    Write-Step "Starting backend at $BackendUrl"
    foreach ($logPath in @($BackendStdoutLog, $BackendStderrLog)) {
        if (Test-Path $logPath) {
            Remove-Item -LiteralPath $logPath -Force
        }
    }
    $process = Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "backend.main:app", "--host", $BackendHost, "--port", [string]$BackendPort) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $BackendStdoutLog `
        -RedirectStandardError $BackendStderrLog `
        -PassThru

    for ($index = 0; $index -lt 40; $index += 1) {
        if (Test-Health) {
            Write-Step "Backend is ready"
            return $process
        }
        if ($process.HasExited) {
            break
        }
        Start-Sleep -Milliseconds 500
    }

    $tail = ""
    foreach ($logPath in @($BackendStdoutLog, $BackendStderrLog)) {
        if (Test-Path $logPath) {
            $tail += "`n[$logPath]`n"
            $tail += (Get-Content -Path $logPath -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
        }
    }
    throw "Backend did not become ready. Log tail:`n$tail"
}

function Start-Frontend([string]$Npm) {
    $existing = Get-ListenProcess $FrontendPort
    if ($existing) {
        $commandLine = [string]$existing.CommandLine
        if (Test-WorkspaceProcess $commandLine) {
            try {
                $response = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                    Write-Step "Frontend already running at $FrontendUrl"
                    return $null
                }
            }
            catch {
                Write-Step "Stopping stale frontend process on port $FrontendPort"
                Stop-Process -Id $existing.ProcessId -Force -ErrorAction Stop
                Start-Sleep -Milliseconds 500
            }
        }
        else {
            throw "Frontend port $FrontendPort is already used by another process: $commandLine"
        }
    }

    foreach ($logPath in @($FrontendStdoutLog, $FrontendStderrLog)) {
        if (Test-Path $logPath) {
            Remove-Item -LiteralPath $logPath -Force
        }
    }

    Write-Step "Starting frontend at $FrontendUrl"
    $process = Start-Process `
        -FilePath $Npm `
        -ArgumentList @("--prefix", (Join-Path $Root "desktop"), "run", "dev:web", "--", "--port", [string]$FrontendPort) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $FrontendStdoutLog `
        -RedirectStandardError $FrontendStderrLog `
        -PassThru

    for ($index = 0; $index -lt 40; $index += 1) {
        try {
            $response = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                Write-Step "Frontend is ready"
                return $process
            }
        }
        catch {
            if ($process.HasExited) {
                break
            }
            Start-Sleep -Milliseconds 500
        }
    }

    $tail = ""
    foreach ($logPath in @($FrontendStdoutLog, $FrontendStderrLog)) {
        if (Test-Path $logPath) {
            $tail += "`n[$logPath]`n"
            $tail += (Get-Content -Path $logPath -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
        }
    }
    throw "Frontend did not become ready. Log tail:`n$tail"
}

try {
    Set-Location $Root
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

    $env:MAVRIS_ENV = if ($env:MAVRIS_ENV) { $env:MAVRIS_ENV } else { "development" }
    $env:MAVRIS_BACKEND_URL = $BackendUrl
    $env:MARVIS_CONFIG_DIR = $Root

    $python = Find-Python
    $npm = Find-Npm

    Ensure-NodeDependencies $npm
    Ensure-PythonDependencies $python
    $startedBackend = Start-Backend $python
    $startedFrontend = Start-Frontend $npm

    if ($CheckOnly) {
        Write-Step "Startup check passed"
        exit 0
    }

    Write-Step "Mavris is ready"
    Write-Host "Open: $FrontendUrl"
    Start-Process $FrontendUrl | Out-Null
    if ($Detached) {
        $leaveProcessesRunning = $true
        Write-Host "Mavris is running in the background. You can close this window."
        exit 0
    }

    Write-Host "Close this window or press Ctrl+C to stop this development session."
    while ($true) {
        if ($startedBackend -and $startedBackend.HasExited) {
            throw "Backend process exited. Check $BackendStderrLog"
        }
        if ($startedFrontend -and $startedFrontend.HasExited) {
            throw "Frontend process exited. Check $FrontendStderrLog"
        }
        Start-Sleep -Seconds 2
    }
}
catch {
    Write-Host ""
    Write-Host "Startup failed:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
finally {
    if (-not $leaveProcessesRunning -and $startedFrontend -and -not $startedFrontend.HasExited) {
        Write-Step "Stopping frontend started by this launcher"
        Stop-Process -Id $startedFrontend.Id -Force -ErrorAction SilentlyContinue
    }
    if (-not $leaveProcessesRunning -and $startedBackend -and -not $startedBackend.HasExited) {
        Write-Step "Stopping backend started by this launcher"
        Stop-Process -Id $startedBackend.Id -Force -ErrorAction SilentlyContinue
    }
}
