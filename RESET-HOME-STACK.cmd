@echo off
title Reset Home Stack (clean stop then start)
cd /d "%~dp0"
echo.
echo === Clean reset: stop everything, then start showcase stack ===
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-stop-everything.ps1" -BotPort 7002 -AnalyzerPort 9500 -NoWait
timeout /t 8 /nobreak >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-start-everything.ps1" -BotPort 7002 -AnalyzerPort 9500 -NoWait
echo.
echo Done. Hard-refresh Agent Hub in 60 seconds.
timeout /t 5 /nobreak >nul
