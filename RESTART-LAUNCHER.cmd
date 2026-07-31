@echo off

title Recover Fly Desktop Mirror

cd /d "%~dp0"

echo.
echo === Fly desktop mirror recovery ===
echo Bridge :7810 ^| Fly proxy :7002 ^| Analyzer :9001
echo.
echo Fly.io remains the only AI, strategy, and trading owner.
echo This PC starts monitoring and control views only.
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
echo Hard-refresh Agent Hub if its cached status is old.
echo Dashboard: http://127.0.0.1:7002/
echo Analyzer:  http://127.0.0.1:9001/

if "%~1"=="--no-pause" exit /b 0
pause

