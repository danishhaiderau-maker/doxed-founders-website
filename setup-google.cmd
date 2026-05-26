@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-google-oauth.ps1 -OpenConsole
pause
