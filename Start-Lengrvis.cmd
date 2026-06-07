@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo 正在启动 Lengrvis，请稍等...
echo 第一次启动可能需要 1-5 分钟。日志位置：%~dp0logs
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1" -Detached -Desktop
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo Lengrvis 启动失败，退出代码：%EXITCODE%
  echo 请查看 logs 文件夹，或双击 Start-Lengrvis-Debug.cmd 查看最近错误。
  echo.
  echo 按任意键关闭窗口...
  pause >nul
  exit /b %EXITCODE%
)

echo.
echo Lengrvis 已启动。
echo 如果没有看到桌面窗口，请打开：http://127.0.0.1:5173
echo 日志位置：%~dp0logs
echo 此窗口将在几秒后自动关闭。
timeout /t 8 /nobreak >nul
exit /b 0
