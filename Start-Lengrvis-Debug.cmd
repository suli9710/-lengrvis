@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo 正在以调试模式启动 Lengrvis，请保持此窗口打开...
echo 调试模式会启动应用，并只打印已脱敏的最近日志摘要。
echo 日志位置：%~dp0logs
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1" -Desktop
set EXITCODE=%ERRORLEVEL%

echo.
echo Lengrvis 已退出，退出代码：%EXITCODE%
echo.
echo ---- 最近启动日志摘要（已脱敏） ----
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1" -PrintRecentLogs
echo.
echo 下一步：
echo   普通用户：请确认你使用的是完整发布包，重新解压后再启动。
echo   普通用户不要自行编辑 .env 或 config.yaml；配置请在应用设置里完成。
echo   源码开发者：请先运行 scripts\setup_dev.ps1，完成后再启动。
echo.
echo 按任意键关闭窗口...
pause >nul
exit /b %EXITCODE%
