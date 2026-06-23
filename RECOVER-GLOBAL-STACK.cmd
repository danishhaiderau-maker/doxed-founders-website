@echo off
title Recover Global Stack
cd /d "%~dp0"
echo.
echo === Recover global showcase (bridge + bot :7002 + analyzer :9500) ===
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fast-recover-global.ps1"
echo.
if "%~1"=="--no-pause" exit /b %ERRORLEVEL%
pause
