@echo off
title Start Everything (Home Stack)
cd /d "%~dp0"
echo.
echo === One-click home stack ===
echo.
call "%~dp0RESTART-LAUNCHER.cmd" --no-pause
timeout /t 2 /nobreak >nul
echo Starting bot + analyzer + tunnel + control panel + auto-wire...
curl -sS -m 120 "http://127.0.0.1:7810/cmd/start-all"
echo.
echo.
echo Windows opened:
echo   - Doxed Bot :7800
echo   - Doxed Analyzer :9001
echo   - Doxed Cloudflare Tunnel
echo   - Doxed Stack Control Panel
echo   - Auto-wire (when tunnel URL ready)
echo.
echo Refresh Agent Hub in browser. Tunnel URL fills automatically.
pause
