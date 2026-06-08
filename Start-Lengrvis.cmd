@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo 正在启动 Lengrvis，请稍等...
echo 首次打开可能需要 1-5 分钟；启动器不会现场安装依赖。
echo 日志位置：%~dp0logs
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1" -Detached -Desktop
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
  echo.
  echo Lengrvis 启动失败，退出代码：%EXITCODE%
  echo 普通用户：请确认你使用的是完整发布包，并重新解压后再启动。
  echo 不要自行编辑 .env 或 config.yaml；配置请在应用设置里完成。
  echo 源码开发者：请先运行 scripts\setup_dev.ps1，完成后再启动。
  echo 也可以双击 Start-Lengrvis-Debug.cmd 查看最近错误。
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
