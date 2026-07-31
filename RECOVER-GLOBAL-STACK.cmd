@echo off
title Recover Fly Desktop Mirror
cd /d "%~dp0"
echo.
echo === Recover Fly-owned showcase views ===
echo Fly.io remains the only AI, strategy, and trading owner.
echo Desktop starts only dashboard proxy :7002, analyzer :9001, and bridge :7810.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fast-recover-global.ps1"
echo.
if "%~1"=="--no-pause" exit /b %ERRORLEVEL%
pause
