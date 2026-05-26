@echo off
REM Bypasses PowerShell execution policy blocking npm.ps1
cd /d "%~dp0"
node scripts\bootstrap-self-host.mjs
exit /b %ERRORLEVEL%
