@echo off
title Start Stable Home Stack (48h+)
cd /d "%~dp0"
echo.
echo === Doxed stable home stack ===
echo   Bridge + Bot + Analyzer + Named tunnel + 24/7 supervisor
echo.

call "%~dp0RESTART-LAUNCHER.cmd" --no-pause
if errorlevel 1 (
  echo Launcher failed.
  pause
  exit /b 1
)

echo Starting stack via bridge...
curl.exe -sS -m 8 http://127.0.0.1:7810/cmd/start-all
echo.
echo.
echo Leave these windows OPEN on this PC:
echo   - Doxed Home Bridge :7810
echo   - Doxed Bot :7002
echo   - Doxed Analyzer :9500
echo   - Doxed Cloudflare Tunnel  (or hidden cloudflared in logs/)
echo.
echo Supervisor log: .home-stack-supervisor.log
echo Health watch log: .home-stack-watch.log
echo.
echo Run watch:  powershell -File scripts\home-stack-watch.ps1 -Hours 48 -IntervalSec 300
echo.
if "%~1"=="--no-pause" exit /b 0
pause
