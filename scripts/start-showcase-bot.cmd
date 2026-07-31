@echo off
REM ====================================================================
REM  DCF desktop mirror autostart entry point.
REM  Target of the already-registered DcfShowcaseBotAutostart task.
REM
REM  SINGLE-OWNER CONTRACT:
REM    Fly.io owns AI, strategy decisions, orders, and trading state.
REM    Windows starts only the :7002 Fly dashboard proxy, Fly data sync,
REM    and the :9001 analyzer over that mirrored data.
REM
REM  This scheduled-task path intentionally has NO legacy fallback and
REM  ignores every legacy opt-in. It can never start a second Python bot,
REM  supervisor, relay publisher, or Cloudflare tunnel.
REM ====================================================================
setlocal
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start-fly-desktop-mirror.ps1" -NoWait
set "DCF_MIRROR_EXIT=%ERRORLEVEL%"
endlocal & exit /b %DCF_MIRROR_EXIT%
