@echo off
title Home Bot Starter — DoxxedCrypto
echo ============================================
echo   Home Bot Starter
echo ============================================
echo.

echo [1/4] Killing stale processes...
taskkill /F /IM python.exe 2>nul
taskkill /F /IM cloudflared.exe 2>nul
timeout /t 3 /nobreak >nul
echo   Done.
echo.

echo [2/4] Starting bot on :7002...
cd /d "C:\Users\user\Desktop\Final Bots\doxedcryptofounder\services\btc-conservative-agent"
start "BTC Bot :7002" C:\Python314\python.exe bot.py
echo   Bot window opened.
echo.

echo [3/4] Starting Cloudflare tunnel...
start "CF Tunnel" cloudflared tunnel --url http://127.0.0.1:7002
echo   Tunnel window opened.
echo.

echo [4/4] Waiting 35 seconds for bot to boot...
timeout /t 35 /nobreak >nul

echo   Fetching tunnel URL...
for /f "delims=" %%A in ('powershell -Command "& { try { (Invoke-WebRequest -Uri 'http://127.0.0.1:7002/api/ping' -TimeoutSec 5).Content | ConvertFrom-Json | ForEach-Object { $_.ok } } catch { 'no' } }"') do set BOT_OK=%%A
if not "%BOT_OK%"=="True" (
    echo   WARNING: Bot not ready yet. Wait 15s more and re-run:
    echo   npm run wire:home-bot -- "YOUR_TUNNEL_URL" --skip-health-check
    echo.
    pause
    exit /b 1
)

echo   Bot OK. Running wire...
cd /d "C:\Users\user\Desktop\Final Bots\doxedcryptofounder"

:: Find the active tunnel URL
powershell -Command "Start-Sleep -Seconds 2; $ports = Get-NetTCPConnection -OwningProcess (Get-Process cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id) -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort }; if (-not $ports) { Write-Host 'Tunnel process not found — paste the URL from the CF Tunnel window.'; exit 1 }"

echo.
echo ============================================
echo   Next step — paste your tunnel URL below.
echo   Find it in the "CF Tunnel" window (ends in .trycloudflare.com)
echo ============================================
set /p TUNNEL_URL="Tunnel URL: "
npm run wire:home-bot -- "%TUNNEL_URL%" --skip-health-check

echo.
echo ============================================
echo   START-HOME complete!
echo   Agent Hub: https://doxxedcrypto.digital/agent-hub/conservative-btc
echo ============================================
pause
