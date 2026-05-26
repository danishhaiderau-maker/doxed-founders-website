@echo off

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev-tunnel.ps1

pause
