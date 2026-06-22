@echo off
title Setup tunnel via Cloudflare API (no browser login)
cd /d "%~dp0"
echo.
echo === Cloudflare API tunnel setup ===
echo URL: https://bot.doxxedcrypto.digital
echo.
echo You need a Cloudflare API token ONCE (not your login password):
echo   1. In dashboard: doxxedcrypto.digital -^> API box -^> "Get your API token"
echo   2. Create Custom Token with:
echo        Account - Cloudflare Tunnel - Edit
echo        Zone doxxedcrypto.digital - DNS - Edit
echo   3. Paste token below when prompted
echo.
set /p CLOUDFLARE_API_TOKEN=Paste API token (hidden on screen is OK): 
if "%CLOUDFLARE_API_TOKEN%"=="" (
  echo No token entered.
  pause
  exit /b 1
)
set CLOUDFLARE_ACCOUNT_ID=242582298202462d75198184516c54d2
set CLOUDFLARE_ZONE_ID=e5b41e1d9809507e75ecd826d8d66bef
call npm run setup:named-tunnel:api
if errorlevel 1 pause
exit /b %errorlevel%
