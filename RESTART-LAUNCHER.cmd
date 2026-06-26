@echo off

title Recover Home Stack (bridge + bot + analyzer + tunnel)

cd /d "%~dp0"

echo.
echo === Full home stack recovery ===
echo Bridge :7810 ^| Bot :7002 ^| Analyzer :9500 ^| Tunnel
echo.
echo Agent Hub buttons only work when bridge :7810 is already running.
echo This script starts everything from scratch on your PC.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fast-recover-global.ps1"

if errorlevel 1 (
  echo Recovery failed - check the bridge window for errors.
  if not "%~1"=="--no-pause" pause
  exit /b 1
)

curl -sS -m 4 http://127.0.0.1:7810/health
echo.
echo.
echo Wait 30-60 seconds, then hard-refresh Agent Hub (Ctrl+F5).
echo Keep open: Doxed Home Bridge, Doxed Bot :7002, Doxed Analyzer :9500, Cloudflare Tunnel.

if "%~1"=="--no-pause" exit /b 0
pause

