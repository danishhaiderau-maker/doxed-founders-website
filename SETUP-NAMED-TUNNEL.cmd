@echo off
title Setup stable bot tunnel (bot.doxxedcrypto.digital)
cd /d "%~dp0"
echo.
echo === Permanent tunnel setup ===
echo Stable URL: https://bot.doxxedcrypto.digital
echo.
echo If you logged into Cloudflare DASHBOARD only, you still need tunnel login on THIS PC.
echo A browser window will open for cloudflared tunnel login (one-time).
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-named-tunnel-full.ps1"
if errorlevel 1 (
  echo.
  echo Setup failed. Common fix: run in PowerShell:
  echo   cloudflared tunnel login
  echo Then re-run this file.
  pause
  exit /b 1
)
echo.
echo Done. Restart launcher and click Start everything if bot is not running.
pause
