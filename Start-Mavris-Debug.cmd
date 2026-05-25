@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1"
set EXITCODE=%ERRORLEVEL%

echo.
echo Mavris exited with code: %EXITCODE%
echo.
echo ---- backend.err.log ----
if exist "%~dp0logs\backend.err.log" type "%~dp0logs\backend.err.log"
echo.
echo ---- frontend.err.log ----
if exist "%~dp0logs\frontend.err.log" type "%~dp0logs\frontend.err.log"
echo.
pause
exit /b %EXITCODE%
