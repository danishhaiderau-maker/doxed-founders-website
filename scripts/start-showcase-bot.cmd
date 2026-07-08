@echo off
REM ====================================================================
REM  DCF showcase bot autostart entry point.
REM  Target of the DcfShowcaseBotAutostart scheduled task (AtLogon +30s,
REM  daily 04:00 +30s).
REM
REM  FAIL-SAFE GUARD: if the bot is already bound to :7002 by a python
REM  process, this script exits 0 IMMEDIATELY without invoking the
REM  orchestrator. Prevents the 04:00 scheduled task from racing the
REM  supervisor when the bot is already healthy (observed 04:16-04:35
REM  crash clusters). The supervisor stays in charge.
REM ====================================================================
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-showcase-bot-guard.ps1"
if %ERRORLEVEL% EQU 99 goto :skipped
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0home-stack-start-everything.ps1" -NoWait
:skipped
endlocal
