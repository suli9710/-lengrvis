param(
    [string]$BackendHost = "127.0.0.1",
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [switch]$SkipInstall,
    [switch]$InstallMissingDependencies,
    [switch]$EnableLanTls,
    [string]$TlsCertFile = "",
    [string]$TlsKeyFile = "",
    [switch]$CheckOnly,
    [switch]$Detached,
    [switch]$Desktop
)

$ErrorActionPreference = "Stop"
try {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [Console]::OutputEncoding = $utf8NoBom
    [Console]::InputEncoding = $utf8NoBom
    $OutputEncoding = $utf8NoBom
}
catch {
}
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$DesktopDir = Join-Path $Root "desktop"
$BackendScheme = "http"
$BackendUrl = "http://$BackendHost`:$BackendPort"
$FrontendUrl = "http://127.0.0.1`:$FrontendPort"
$LogDir = Join-Path $Root "logs"
$LogStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$BackendStdoutLog = Join-Path $LogDir "backend.$BackendPort.$LogStamp.out.log"
$BackendStderrLog = Join-Path $LogDir "backend.$BackendPort.$LogStamp.err.log"
$DesktopStdoutLog = Join-Path $LogDir "desktop.out.log"
$DesktopStderrLog = Join-Path $LogDir "desktop.err.log"
$FrontendStdoutLog = Join-Path $LogDir "frontend.$FrontendPort.$LogStamp.out.log"
$FrontendStderrLog = Join-Path $LogDir "frontend.$FrontendPort.$LogStamp.err.log"
$startedBackend = $null
$startedFrontend = $null
$startedDesktop = $null
$leaveProcessesRunning = $false

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Format-DependencyList([string[]]$Items) {
    return ($Items | ForEach-Object { "  - $_" }) -join "`n"
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
    throw "未找到 Python。请先安装 Python 3.12+，或在项目目录创建 .venv。"
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
    throw "未找到 npm。请先安装 Node.js 20+。"
}

function Test-TruthyEnv([string]$Value) {
    return $Value -and $Value.ToLowerInvariant() -in @("1", "true", "yes", "on")
}

function Resolve-LaunchPath([string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $Root $Path
}

function Resolve-LanTlsConfig {
    $cert = if ($TlsCertFile) { $TlsCertFile } elseif ($env:LENGRVIS_LAN_TLS_CERT_FILE) { $env:LENGRVIS_LAN_TLS_CERT_FILE } else { "" }
    $key = if ($TlsKeyFile) { $TlsKeyFile } elseif ($env:LENGRVIS_LAN_TLS_KEY_FILE) { $env:LENGRVIS_LAN_TLS_KEY_FILE } else { "" }
    $enabled = [bool]$EnableLanTls -or (Test-TruthyEnv $env:LENGRVIS_LAN_TLS_ENABLED) -or [bool]$cert -or [bool]$key

    if (-not $enabled) {
        return [pscustomobject]@{ Enabled = $false; CertFile = ""; KeyFile = "" }
    }
    if (-not $cert -or -not $key) {
        throw "启用 LAN HTTPS 需要同时提供证书和私钥。请使用 -TlsCertFile/-TlsKeyFile，或设置 LENGRVIS_LAN_TLS_CERT_FILE/LENGRVIS_LAN_TLS_KEY_FILE。"
    }

    $certPath = Resolve-LaunchPath $cert
    $keyPath = Resolve-LaunchPath $key
    if (-not (Test-Path -LiteralPath $certPath -PathType Leaf)) {
        throw "LAN HTTPS 证书文件不存在：$certPath"
    }
    if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
        throw "LAN HTTPS 私钥文件不存在：$keyPath"
    }

    return [pscustomobject]@{ Enabled = $true; CertFile = $certPath; KeyFile = $keyPath }
}

function Test-Health {
    try {
        $healthUrl = "$BackendUrl/api/health"
        if ($BackendScheme -eq "https") {
            $invokeCommand = Get-Command Invoke-WebRequest
            if ($invokeCommand.Parameters.ContainsKey("SkipCertificateCheck")) {
                $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2 -SkipCertificateCheck
                return $response.StatusCode -ge 200 -and $response.StatusCode -lt 300
            }
            $previousCallback = [System.Net.ServicePointManager]::ServerCertificateValidationCallback
            try {
                [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
                $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
            }
            finally {
                [System.Net.ServicePointManager]::ServerCertificateValidationCallback = $previousCallback
            }
        }
        else {
            $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        }
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

function Test-PackagedLengrvisBackend([string]$CommandLine) {
    $lower = if ($CommandLine) { $CommandLine.ToLowerInvariant() } else { "" }
    return $lower.Contains("\lengrvis\resources\backend\backend.exe")
}

function Test-UvicornLengrvisBackend([string]$CommandLine) {
    $lower = if ($CommandLine) { $CommandLine.ToLowerInvariant() } else { "" }
    return $lower.Contains("uvicorn") -and ($lower.Contains("backend.main:app") -or $lower.Contains("backend.main:full_app"))
}

function Stop-FullBackendIfWorkspaceOwned {
    $fullBackendProcess = Get-ListenProcess 8001
    if (-not $fullBackendProcess) {
        return
    }
    $commandLine = [string]$fullBackendProcess.CommandLine
    if ((Test-WorkspaceProcess $commandLine) -or $commandLine.ToLowerInvariant().Contains("backend.main:full_app")) {
        Write-Step "正在关闭旧的完整后端进程（端口 8001）"
        Stop-Process -Id $fullBackendProcess.ProcessId -Force -ErrorAction Stop
        Start-Sleep -Milliseconds 500
    }
}

function Stop-WorkspaceProcessOnPort([int]$Port, [string]$Purpose) {
    $process = Get-ListenProcess $Port
    if (-not $process) {
        return
    }

    $commandLine = [string]$process.CommandLine
    if (Test-WorkspaceProcess $commandLine) {
        Write-Step "正在关闭旧的 $Purpose 进程（端口 $Port）"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        Start-Sleep -Milliseconds 500
        return
    }

    throw "端口 $Port 已被其他程序占用：$commandLine"
}

function Stop-WorkspaceListenerOnPort([int]$Port, [string]$Purpose) {
    try {
        $process = Get-ListenProcess $Port
        if (-not $process) {
            return
        }

        $commandLine = [string]$process.CommandLine
        if (Test-WorkspaceProcess $commandLine) {
            Write-Step "正在清理本次启动留下的 $Purpose 进程（端口 $Port）"
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
        }
    }
    catch {
        Write-Host "清理 $Purpose 进程时遇到问题：$($_.Exception.Message)" -ForegroundColor Yellow
    }
}

function Ensure-NodeDependencies([string]$Npm, [bool]$NeedsDesktop) {
    # Legacy switch: keep accepting callers that explicitly opted out of install/check.
    if ($SkipInstall) {
        return
    }

    $requiredPaths = @(
        (Join-Path $DesktopDir "node_modules\.bin\vite.cmd")
    )
    if ($NeedsDesktop) {
        $requiredPaths += (Join-Path $DesktopDir "node_modules\electron\dist\electron.exe")
    }

    $missingDependencies = @()
    foreach ($path in $requiredPaths) {
        if (-not (Test-Path $path)) {
            $missingDependencies += $path
        }
    }

    if ($missingDependencies.Count -eq 0) {
        return
    }

    if (-not $InstallMissingDependencies) {
        $missingList = Format-DependencyList $missingDependencies
        throw @"
缺少桌面/前端依赖：
$missingList

正式/产品启动不会现场运行 npm install。
开发环境请先运行：
  .\scripts\dev.ps1 -InstallMissingDependencies
也可以手动运行：
  $Npm --prefix desktop install
如确需由本启动脚本安装，重新运行：
  .\scripts\start_app.ps1 -InstallMissingDependencies
"@
    }

    Write-Step "正在安装桌面依赖（显式开发安装模式）"
    & $Npm --prefix $DesktopDir install
    if ($LASTEXITCODE -ne 0) {
        throw "桌面依赖安装失败。请查看上方输出或 logs 文件夹。"
    }

    $remainingDependencies = @()
    foreach ($path in $requiredPaths) {
        if (-not (Test-Path $path)) {
            $remainingDependencies += $path
        }
    }
    if ($remainingDependencies.Count -ne 0) {
        throw "桌面依赖安装后仍缺少：`n$(Format-DependencyList $remainingDependencies)"
    }
}

function Get-MissingPythonRequirements([string]$Python) {
    $requirementsPath = Join-Path $Root "backend\requirements.txt"
    $previousRequirementsPath = $env:LENGRVIS_REQUIREMENTS_CHECK_PATH
    $dependencyCheckScript = @'
import importlib.metadata as metadata
import os
import pathlib
import platform
import re
import sys


def marker_applies(marker):
    marker = marker.strip()
    for key, actual in (
        ("platform_system", platform.system()),
        ("sys_platform", sys.platform),
    ):
        match = re.fullmatch(rf"{key}\s*(==|!=)\s*['\"]([^'\"]+)['\"]", marker)
        if match:
            operator, expected = match.groups()
            return actual == expected if operator == "==" else actual != expected
    return True


def requirement_name(line):
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("-"):
        return None
    line = re.split(r"\s+#", line, 1)[0].strip()
    requirement, _, marker = line.partition(";")
    if marker and not marker_applies(marker):
        return None
    requirement = requirement.strip()
    if "://" in requirement or requirement.startswith((".", "/")):
        return None
    return re.split(r"\s*(?:\[|===|==|~=|!=|<=|>=|<|>)", requirement, 1)[0].strip() or None


requirements_path = pathlib.Path(os.environ["LENGRVIS_REQUIREMENTS_CHECK_PATH"])
missing = []
for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
    name = requirement_name(raw_line)
    if not name:
        continue
    try:
        metadata.distribution(name)
    except metadata.PackageNotFoundError:
        missing.append(name)

if missing:
    print("\n".join(missing))
    sys.exit(1)
'@

    try {
        $env:LENGRVIS_REQUIREMENTS_CHECK_PATH = $requirementsPath
        $output = & $Python -c $dependencyCheckScript 2>&1
        if ($LASTEXITCODE -eq 0) {
            return @()
        }
        return @($output | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() })
    }
    finally {
        if ($null -eq $previousRequirementsPath) {
            Remove-Item Env:\LENGRVIS_REQUIREMENTS_CHECK_PATH -ErrorAction SilentlyContinue
        }
        else {
            $env:LENGRVIS_REQUIREMENTS_CHECK_PATH = $previousRequirementsPath
        }
    }
}

function Ensure-PythonDependencies([string]$Python) {
    # Legacy switch: keep accepting callers that explicitly opted out of install/check.
    if ($SkipInstall) {
        return
    }

    $missingDependencies = @(Get-MissingPythonRequirements $Python)
    if ($missingDependencies.Count -eq 0) {
        return
    }

    if (-not $InstallMissingDependencies) {
        $missingList = Format-DependencyList $missingDependencies
        throw @"
缺少后端 Python 依赖：
$missingList

正式/产品启动不会现场运行 pip install。
开发环境请先运行：
  .\scripts\dev.ps1 -InstallMissingDependencies
也可以手动运行：
  $Python -m pip install -r backend\requirements.txt
如确需由本启动脚本安装，重新运行：
  .\scripts\start_app.ps1 -InstallMissingDependencies
"@
    }

    Write-Step "正在安装后端依赖（显式开发安装模式）"
    & $Python -m pip install -r (Join-Path $Root "backend\requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "后端依赖安装失败。请查看上方输出或 logs 文件夹。"
    }

    $remainingDependencies = @(Get-MissingPythonRequirements $Python)
    if ($remainingDependencies.Count -ne 0) {
        throw "后端依赖安装后仍缺少：`n$(Format-DependencyList $remainingDependencies)"
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

    Write-Step "正在准备桌面窗口"
    & $Npm --prefix $DesktopDir run build:electron
    if ($LASTEXITCODE -ne 0) {
        throw "桌面窗口构建失败。请查看上方输出或 logs 文件夹。"
    }
}

function Start-Backend([string]$Python, [object]$LanTlsConfig) {
    $existing = Get-ListenProcess $BackendPort
    if ($existing) {
        $commandLine = [string]$existing.CommandLine
        if (Test-PackagedLengrvisBackend $commandLine) {
            Write-Step "正在关闭已安装版 Lengrvis 后端，改用当前目录版本"
            Stop-Process -Id $existing.ProcessId -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 700
        }
        elseif ((Test-WorkspaceProcess $commandLine) -or (Test-UvicornLengrvisBackend $commandLine)) {
            if (Test-Health) {
                Write-Step "后端服务已在运行：$BackendUrl"
                return $null
            }
            Stop-Process -Id $existing.ProcessId -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 500
        }
        else {
            throw "后端端口 $BackendPort 已被其他程序占用：$commandLine"
        }
    }

    Write-Step "正在启动后端服务：$BackendUrl"
    Stop-FullBackendIfWorkspaceOwned
    $env:LENGRVIS_FULL_BACKEND = "1"
    $backendArgs = @("-m", "uvicorn", "backend.main:full_app", "--host", $BackendHost, "--port", [string]$BackendPort)
    if ($LanTlsConfig.Enabled) {
        $backendArgs += @("--ssl-certfile", [string]$LanTlsConfig.CertFile, "--ssl-keyfile", [string]$LanTlsConfig.KeyFile)
    }
    $process = Start-Process `
        -FilePath $Python `
        -ArgumentList $backendArgs `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $BackendStdoutLog `
        -RedirectStandardError $BackendStderrLog `
        -PassThru

    for ($index = 0; $index -lt 40; $index += 1) {
        if (Test-Health) {
            Write-Step "后端服务已启动"
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
    throw "后端服务启动超时。请查看日志：$BackendStdoutLog 和 $BackendStderrLog`n最近日志：$tail"
}

function Start-Frontend([string]$Npm) {
    $existing = Get-ListenProcess $FrontendPort
    if ($existing) {
        $commandLine = [string]$existing.CommandLine
        if (Test-WorkspaceProcess $commandLine) {
            try {
                $response = Invoke-WebRequest -Uri $FrontendUrl -UseBasicParsing -TimeoutSec 2
                if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                    Write-Step "界面服务已在运行：$FrontendUrl"
                    return $null
                }
            }
            catch {
                Write-Step "正在关闭旧的界面服务进程（端口 $FrontendPort）"
                Stop-Process -Id $existing.ProcessId -Force -ErrorAction Stop
                Start-Sleep -Milliseconds 500
            }
        }
        else {
            throw "界面服务端口 $FrontendPort 已被其他程序占用：$commandLine"
        }
    }

    Write-Step "正在启动界面服务：$FrontendUrl"
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
                Write-Step "界面服务已启动"
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
    throw "界面服务启动超时。请查看日志：$FrontendStdoutLog 和 $FrontendStderrLog`n最近日志：$tail"
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
        throw "未找到桌面运行时：$electron。请先运行 npm --prefix desktop install。"
    }

    $existing = Get-RunningDesktopProcess $electron
    if ($existing) {
        Write-Step "桌面窗口已在运行"
        return $null
    }

    $logStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $DesktopStdoutLog = Join-Path $LogDir "desktop.$logStamp.out.log"
    $DesktopStderrLog = Join-Path $LogDir "desktop.$logStamp.err.log"
    Set-Variable -Name DesktopStdoutLog -Scope Script -Value $DesktopStdoutLog
    Set-Variable -Name DesktopStderrLog -Scope Script -Value $DesktopStderrLog

    Write-Step "正在打开 Lengrvis 桌面窗口"
    $previousViteDevServerUrl = $env:VITE_DEV_SERVER_URL
    try {
        $env:VITE_DEV_SERVER_URL = $FrontendUrl
        $env:LENGRVIS_BACKEND_URL = $BackendUrl
        $env:LENGRVIS_CONFIG_DIR = $Root
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
            Write-Step "Lengrvis 已交给现有窗口"
            return $null
        }

        $tail = ""
        foreach ($logPath in @($DesktopStdoutLog, $DesktopStderrLog)) {
            if (Test-Path $logPath) {
                $tail += "`n[$logPath]`n"
                $tail += (Get-Content -Path $logPath -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
            }
        }
        throw "桌面窗口启动后立即退出，退出代码 $exitCode。请查看日志：$DesktopStderrLog`n最近日志：$tail"
    }

    Write-Step "桌面窗口已打开"
    return $process
}

try {
    Set-Location $Root
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $lanTlsConfig = Resolve-LanTlsConfig
    if ($lanTlsConfig.Enabled) {
        $BackendScheme = "https"
        $BackendUrl = "${BackendScheme}://$BackendHost`:$BackendPort"
        $env:LENGRVIS_LAN_TLS_ENABLED = "true"
        $env:LENGRVIS_LAN_TLS_CERT_FILE = [string]$lanTlsConfig.CertFile
        $env:LENGRVIS_LAN_TLS_KEY_FILE = [string]$lanTlsConfig.KeyFile
    }
    else {
        $env:LENGRVIS_LAN_TLS_ENABLED = "false"
    }

    if (-not $env:LENGRVIS_ENV) {
        $env:LENGRVIS_ENV = "development"
    }
    $env:LENGRVIS_BACKEND_URL = $BackendUrl
    $env:LENGRVIS_BACKEND_HOST = $BackendHost
    $env:LENGRVIS_BACKEND_PORT = [string]$BackendPort
    $env:LENGRVIS_CONFIG_DIR = $Root

    $python = Find-Python
    $npm = Find-Npm

    Ensure-NodeDependencies $npm ([bool]$Desktop)
    Ensure-PythonDependencies $python
    Ensure-DesktopBuild $npm
    $startedBackend = Start-Backend $python $lanTlsConfig
    $startedFrontend = Start-Frontend $npm

    if ($CheckOnly) {
        Write-Step "启动检查通过"
        exit 0
    }

    if ($Desktop) {
        $startedDesktop = Start-DesktopShell
    }

    Write-Step "Lengrvis 已启动"
    Write-Host "访问地址：$FrontendUrl"
    if (-not $Desktop) {
        Start-Process $FrontendUrl | Out-Null
    }
    if ($Detached) {
        $leaveProcessesRunning = $true
        Write-Host "Lengrvis 正在后台运行。可以关闭这个窗口。"
        exit 0
    }

    Write-Host "保持此窗口打开即可持续运行；关闭窗口或按 Ctrl+C 会停止本次开发会话。"
    while ($true) {
        if ($startedBackend -and $startedBackend.HasExited) {
            throw "后端服务已退出。请查看日志：$BackendStderrLog"
        }
        if ($startedFrontend -and $startedFrontend.HasExited) {
            throw "界面服务已退出。请查看日志：$FrontendStderrLog"
        }
        if ($startedDesktop -and $startedDesktop.HasExited) {
            if ($startedDesktop.ExitCode -eq 0) {
                Write-Step "桌面窗口已关闭"
                exit 0
            }
            throw "桌面窗口已退出。请查看日志：$DesktopStderrLog"
        }
        Start-Sleep -Seconds 2
    }
}
catch {
    Write-Host ""
    Write-Host "启动失败：" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "日志文件夹：$LogDir" -ForegroundColor Yellow
    Write-Host "也可以双击 Start-Lengrvis-Debug.cmd 查看最近错误。" -ForegroundColor Yellow
    exit 1
}
finally {
    if (-not $leaveProcessesRunning -and $startedFrontend -and -not $startedFrontend.HasExited) {
        Write-Step "正在停止本次启动的界面服务"
        Stop-Process -Id $startedFrontend.Id -Force -ErrorAction SilentlyContinue
    }
    if (-not $leaveProcessesRunning -and $startedDesktop -and -not $startedDesktop.HasExited) {
        Write-Step "正在停止本次启动的桌面窗口"
        Stop-Process -Id $startedDesktop.Id -Force -ErrorAction SilentlyContinue
    }
    if (-not $leaveProcessesRunning -and $startedBackend -and -not $startedBackend.HasExited) {
        Write-Step "正在停止本次启动的后端服务"
        Stop-Process -Id $startedBackend.Id -Force -ErrorAction SilentlyContinue
    }
    if (-not $leaveProcessesRunning) {
        if ($startedFrontend) {
            Stop-WorkspaceListenerOnPort $FrontendPort "界面服务"
        }
        if ($startedBackend) {
            Stop-WorkspaceListenerOnPort $BackendPort "后端服务"
        }
    }
}
