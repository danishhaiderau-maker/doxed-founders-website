@echo off

title Reset Home Stack (clean stop then start)

cd /d "%~dp0"

echo.
echo === Clean reset: stop everything, wait 8s, start showcase stack ===
echo.
echo Order: 1) Stop bot :7002 + analyzer :9500 + tunnel
echo        2) Wait 8 seconds
echo        3) Start bridge + bot + analyzer + tunnel
echo.
echo /api/ping on :7002 responds in ~2s while bot loads.
echo Full dashboard takes 60-90 seconds on home PC — be patient.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-stop-everything.ps1" -BotPort 7002 -AnalyzerPort 9500 -NoWait

timeout /t 8 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-start-everything.ps1" -BotPort 7002 -AnalyzerPort 9500 -NoWait

echo.
echo Done. Hard-refresh Agent Hub at 30s / 60s / 90s or click Reset home stack in command center.

timeout /t 5 /nobreak >nul
