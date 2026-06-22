@echo off
title Name.com API setup (bot DNS)
cd /d "%~dp0"
echo.
echo === Name.com API for bot.doxxedcrypto.digital ===
echo Cloudflare API cannot change Name.com nameservers.
echo This adds ONE CNAME at Name.com (keeps main site on Vercel).
echo.
echo Create token: https://www.name.com/account/settings/api
echo.
set /p NAMECOM_USERNAME=Name.com username: 
set /p NAMECOM_API_TOKEN=Name.com API token: 
if "%NAMECOM_USERNAME%"=="" goto bad
if "%NAMECOM_API_TOKEN%"=="" goto bad
(
  echo NAMECOM_USERNAME=%NAMECOM_USERNAME%
  echo NAMECOM_API_TOKEN=%NAMECOM_API_TOKEN%
)>"..\doxedcryptofounder-secrets\vault\.env.namecom"
echo Saved to vault\.env.namecom
call npm run finish:home-dns
if errorlevel 1 pause
exit /b %errorlevel%
:bad
echo Missing username or token.
pause
exit /b 1
