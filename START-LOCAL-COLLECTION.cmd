@echo off
title Local Collection (bot :7002 / analyzer :9500)
cd /d "%~dp0"
echo.
echo Local data collection ONLY - separate from doxxedcrypto production (:7800/:9001)
echo Ports frozen in config\local-collection.lock.json
echo Data folder: services\btc-conservative-agent\local-collection-data
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-local-collection.ps1" %*
echo.
if "%~1"=="--no-pause" exit /b 0
pause
