@echo off
title Setup stable bot tunnel (bot.doxxedcrypto.digital)
cd /d "%~dp0"
if not exist "%~dp0scripts\setup-named-tunnel-full.ps1" (
  echo ERROR: setup script not found. Double-click this file in Explorer - do not paste into PowerShell.
  pause
  exit /b 1
)
echo.
echo === Permanent tunnel setup (browser login) ===
echo Prefer API? Use SETUP-NAMED-TUNNEL-API.cmd instead (no browser).
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-named-tunnel-full.ps1"
if errorlevel 1 (
  echo Setup failed. Try SETUP-NAMED-TUNNEL-API.cmd with an API token instead.
  pause
  exit /b 1
)
echo Done. Run RESTART-LAUNCHER.cmd then Start everything.
pause
