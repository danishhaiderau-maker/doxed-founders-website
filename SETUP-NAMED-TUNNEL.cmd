@echo off
title Setup stable bot tunnel (bot.doxxedcrypto.digital)
cd /d "%~dp0"
echo.
echo === Permanent tunnel setup (one-time) ===
echo.
echo Quick trycloudflare URLs die when the tunnel window closes.
echo This sets up a STABLE URL: https://bot.doxxedcrypto.digital
echo.
echo STEP 1 - Run these in an elevated or normal PowerShell if not done yet:
echo   cloudflared tunnel login
echo   cloudflared tunnel create doxed-btc-bot
echo   cloudflared tunnel route dns doxed-btc-bot bot.doxxedcrypto.digital
echo.
echo STEP 2 - Install as Windows service (survives reboot):
echo   npm run install:home-bot-tunnel-service
echo.
echo STEP 3 - Wire to site:
echo   npm run wire:home-bot -- https://bot.doxxedcrypto.digital
echo.
set /p RUN="Run service install now? [Y/n] "
if /i "%RUN%"=="n" goto :eof
powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-named-tunnel-service.ps1"
pause
