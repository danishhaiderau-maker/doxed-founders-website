@echo off

title Restart Home Command Bridge

cd /d "%~dp0"

echo.

echo === Restarting home command bridge (:7810) ===

echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ensure-home-bridge.ps1" -Force

if errorlevel 1 (

  echo Bridge failed to start.

  if not "%~1"=="--no-pause" pause

  exit /b 1

)

curl -sS -m 4 http://127.0.0.1:7810/health

echo.

echo.

if "%~1"=="--no-pause" exit /b 0

echo If you see {"ok":true...} above, hard-refresh Agent Hub and use Start all global.

pause

