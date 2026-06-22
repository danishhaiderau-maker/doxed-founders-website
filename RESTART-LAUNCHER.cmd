@echo off
title Restart Home Command Bridge
cd /d "%~dp0"
echo Stopping old launcher on :7810...
wmic process where "CommandLine like '%%home-stack-launcher%%'" call terminate >nul 2>&1
timeout /t 2 /nobreak >nul
echo Starting fresh launcher...
start "Doxed Home Bridge" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-launcher.ps1"
timeout /t 3 /nobreak >nul
curl -sS -m 4 http://127.0.0.1:7810/health
echo.
if "%~1"=="--no-pause" exit /b 0
echo If you see {"ok":true...} above, refresh Agent Hub and click Start everything.
pause
