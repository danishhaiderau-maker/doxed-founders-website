@echo off

title Stop Home Stack (no restart)

cd /d "%~dp0"

echo.
echo === Stop showcase stack only — bot :7002 will NOT restart ===
echo.
echo Stops: bot :7002, analyzer :9500, tunnel, supervisor
echo Keeps: bridge :7810 (Agent Hub command center)
echo.
echo To stop AND restart use RESET-HOME-STACK.cmd instead.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-stop-everything.ps1" -BotPort 7002 -AnalyzerPort 9500

timeout /t 5 /nobreak >nul
