@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo 正在以调试模式启动 Lengrvis，请保持此窗口打开...
echo 日志位置：%~dp0logs
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1" -Desktop
set EXITCODE=%ERRORLEVEL%

echo.
echo Lengrvis 已退出，退出代码：%EXITCODE%
echo.
echo ---- 最近的后端错误日志 ----
for /f "delims=" %%F in ('dir /b /a-d /o-d "%~dp0logs\backend*.err.log" 2^>nul') do (
  echo.
  echo ---- logs\%%F ----
  type "%~dp0logs\%%F"
)
echo.
echo ---- 最近的界面错误日志 ----
for /f "delims=" %%F in ('dir /b /a-d /o-d "%~dp0logs\frontend*.err.log" 2^>nul') do (
  echo.
  echo ---- logs\%%F ----
  type "%~dp0logs\%%F"
)
echo.
echo ---- 最近的桌面窗口错误日志 ----
for /f "delims=" %%F in ('dir /b /a-d /o-d "%~dp0logs\desktop*.err.log" 2^>nul') do (
  echo.
  echo ---- logs\%%F ----
  type "%~dp0logs\%%F"
)
echo.
echo 按任意键关闭窗口...
pause >nul
exit /b %EXITCODE%
