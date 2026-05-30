@echo off

cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev-lan.ps1

pause

