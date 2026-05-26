param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$SkipInstall,
    [switch]$CheckOnly,
    [switch]$Detached,
    [switch]$Desktop
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$DesktopDir = Join-Path $Root "desktop"
$BackendUrl = "http://$BackendHost`:$BackendPort"
$FrontendUrl = "http://127.0.0.1`:$FrontendPort"
$LogDir = Join-Path $Root "logs"
$BackendStdoutLog = Join-Path $LogDir "backend.out.log"
$BackendStderrLog = Join-Path $LogDir "backend.err.log"
$DesktopStdoutLog = Join-Path $LogDir "desktop.out.log"
$DesktopStderrLog = Join-Path $LogDir "desktop.err.log"
$FrontendStdoutLog = Join-Path $LogDir "frontend.out.log"
$FrontendStderrLog = Join-Path $LogDir "frontend.err.log"
$startedBackend = $null
$startedFrontend = $null
$startedDesktop = $null
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

function Ensure-NodeDependencies([string]$Npm, [bool]$NeedsDesktop) {
    if ($SkipInstall) {
        return
    }

    $requiredPaths = @(
        (Join-Path $DesktopDir "node_modules\.bin\vite.cmd")
    )
    if ($NeedsDesktop) {
        $requiredPaths += (Join-Path $DesktopDir "node_modules\electron\dist\electron.exe")
    }

    $missingDependency = $false
    foreach ($path in $requiredPaths) {
        if (-not (Test-Path $path)) {
            $missingDependency = $true
            break
        }
    }

    if ($missingDependency) {
        Write-Step "Installing desktop dependencies"
        & $Npm --prefix $DesktopDir install
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
        & $Python -c "import bs4, croniter, docx, fastapi, httpx, jwt, numpy, openpyxl, pandas, psutil, pydantic, pypdf, pytesseract, send2trash, uvicorn, watchdog, yaml; from PIL import Image; from pptx import Presentation; import playwright.sync_api" *> $null
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

function Test-DesktopBuildFresh {
    $outputs = @(
        (Join-Path $DesktopDir "dist\main\main.js"),
        (Join-Path $DesktopDir "dist\preload\preload.js")
    )

    foreach ($output in $outputs) {
        if (-not (Test-Path $output)) {
            return $false
        }
    }

    $oldestOutput = Get-Item -LiteralPath $outputs | Sort-Object LastWriteTimeUtc | Select-Object -First 1
    $sourceDirs = @(
        (Join-Path $DesktopDir "src\main"),
        (Join-Path $DesktopDir "src\preload"),
        (Join-Path $DesktopDir "src\shared")
    ) | Where-Object { Test-Path $_ }
    $newestSource = Get-ChildItem -Path $sourceDirs -Recurse -File -Include *.ts,*.tsx -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1

    return -not $newestSource -or $newestSource.LastWriteTimeUtc -le $oldestOutput.LastWriteTimeUtc
}

function Ensure-DesktopBuild([string]$Npm) {
    if (-not $Desktop) {
        return
    }

    if (Test-DesktopBuildFresh) {
        return
    }

    Write-Step "Building desktop shell"
    & $Npm --prefix $DesktopDir run build:electron
    if ($LASTEXITCODE -ne 0) {
        throw "desktop build failed."
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
        -ArgumentList @("--prefix", $DesktopDir, "run", "dev:web", "--", "--port", [string]$FrontendPort, "--strictPort") `
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

function Get-RunningDesktopProcess([string]$ElectronPath) {
    $normalizedElectron = $ElectronPath.ToLowerInvariant()
    $normalizedDesktopDir = $DesktopDir.ToString().ToLowerInvariant()
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $commandLine = if ($_.CommandLine) { $_.CommandLine.ToLowerInvariant() } else { "" }
            $commandLine.Contains($normalizedElectron) -and $commandLine.Contains($normalizedDesktopDir)
        } |
        Select-Object -First 1
}

function Start-DesktopShell {
    $electron = Join-Path $DesktopDir "node_modules\electron\dist\electron.exe"
    if (-not (Test-Path $electron)) {
        throw "Electron runtime was not found at $electron. Run npm --prefix desktop install first."
    }

    $existing = Get-RunningDesktopProcess $electron
    if ($existing) {
        Write-Step "Desktop app already running"
        return $null
    }

    $logStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $DesktopStdoutLog = Join-Path $LogDir "desktop.$logStamp.out.log"
    $DesktopStderrLog = Join-Path $LogDir "desktop.$logStamp.err.log"
    Set-Variable -Name DesktopStdoutLog -Scope Script -Value $DesktopStdoutLog
    Set-Variable -Name DesktopStderrLog -Scope Script -Value $DesktopStderrLog

    Write-Step "Starting desktop app"
    $previousViteDevServerUrl = $env:VITE_DEV_SERVER_URL
    try {
        $env:VITE_DEV_SERVER_URL = $FrontendUrl
        $env:MAVRIS_BACKEND_URL = $BackendUrl
        $env:MARVIS_CONFIG_DIR = $Root
        $process = Start-Process `
            -FilePath $electron `
            -ArgumentList @(".") `
            -WorkingDirectory $DesktopDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput $DesktopStdoutLog `
            -RedirectStandardError $DesktopStderrLog `
            -PassThru
    }
    finally {
        if ($null -eq $previousViteDevServerUrl) {
            Remove-Item Env:\VITE_DEV_SERVER_URL -ErrorAction SilentlyContinue
        }
        else {
            $env:VITE_DEV_SERVER_URL = $previousViteDevServerUrl
        }
    }

    for ($index = 0; $index -lt 20; $index += 1) {
        if ($process.HasExited) {
            break
        }
        Start-Sleep -Milliseconds 250
    }

    if ($process.HasExited) {
        $process.Refresh()
        $exitCode = $process.ExitCode
        if ($null -eq $exitCode -or $exitCode -eq 0) {
            Write-Step "Desktop app handed off to an existing Mavris window"
            return $null
        }

        $tail = ""
        foreach ($logPath in @($DesktopStdoutLog, $DesktopStderrLog)) {
            if (Test-Path $logPath) {
                $tail += "`n[$logPath]`n"
                $tail += (Get-Content -Path $logPath -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
            }
        }
        throw "Desktop app exited during startup with code $exitCode. Log tail:`n$tail"
    }

    Write-Step "Desktop app is ready"
    return $process
}

try {
    Set-Location $Root
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

    $env:MAVRIS_ENV = if ($env:MAVRIS_ENV) { $env:MAVRIS_ENV } else { "development" }
    $env:MAVRIS_BACKEND_URL = $BackendUrl
    $env:MARVIS_CONFIG_DIR = $Root

    $python = Find-Python
    $npm = Find-Npm

    Ensure-NodeDependencies $npm ([bool]$Desktop)
    Ensure-PythonDependencies $python
    Ensure-DesktopBuild $npm
    $startedBackend = Start-Backend $python
    $startedFrontend = Start-Frontend $npm

    if ($CheckOnly) {
        Write-Step "Startup check passed"
        exit 0
    }

    if ($Desktop) {
        $startedDesktop = Start-DesktopShell
    }

    Write-Step "Mavris is ready"
    Write-Host "Open: $FrontendUrl"
    if (-not $Desktop) {
        Start-Process $FrontendUrl | Out-Null
    }
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
        if ($startedDesktop -and $startedDesktop.HasExited) {
            if ($startedDesktop.ExitCode -eq 0) {
                Write-Step "Desktop app exited"
                exit 0
            }
            throw "Desktop app exited. Check $DesktopStderrLog"
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
    if (-not $leaveProcessesRunning -and $startedDesktop -and -not $startedDesktop.HasExited) {
        Write-Step "Stopping desktop app started by this launcher"
        Stop-Process -Id $startedDesktop.Id -Force -ErrorAction SilentlyContinue
    }
    if (-not $leaveProcessesRunning -and $startedBackend -and -not $startedBackend.HasExited) {
        Write-Step "Stopping backend started by this launcher"
        Stop-Process -Id $startedBackend.Id -Force -ErrorAction SilentlyContinue
    }
}
