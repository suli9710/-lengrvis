@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1" -Detached -Desktop
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo Mavris failed to start. Exit code: %EXITCODE%
  echo Check logs in the logs folder, then press any key to close.
  pause >nul
)

exit /b %EXITCODE%
