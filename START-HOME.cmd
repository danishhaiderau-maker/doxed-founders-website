@echo off
title Doxed Fly Desktop Mirror
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fast-recover-global.ps1"
pause
