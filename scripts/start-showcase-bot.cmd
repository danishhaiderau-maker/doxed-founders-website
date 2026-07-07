@echo off
REM ====================================================================
REM  DCF showcase bot autostart entry point.
REM  Target of the DcfShowcaseBotAutostart scheduled task (AtLogon +30s).
REM  Launches the home stack orchestrator which starts bot + analyzer +
REM  Cloudflare tunnel + the 24/7 supervisor + the auto-restart monitor
REM  + the bridge watchdog. Safe to re-run: every step is idempotent and
REM  skips components that are already healthy.
REM ====================================================================
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0home-stack-start-everything.ps1" -NoWait
endlocal
