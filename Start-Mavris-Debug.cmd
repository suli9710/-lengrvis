@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1" -Desktop
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
echo ---- desktop err logs ----
for /f "delims=" %%F in ('dir /b /a-d /o-d "%~dp0logs\desktop*.err.log" 2^>nul') do (
  echo.
  echo ---- logs\%%F ----
  type "%~dp0logs\%%F"
)
echo.
pause
exit /b %EXITCODE%
