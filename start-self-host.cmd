@echo off
setlocal
cd /d "%~dp0"
echo.
echo === Starting self-host servers ===
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-self-host.ps1"
exit /b 0
