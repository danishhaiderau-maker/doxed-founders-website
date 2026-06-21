@echo off
title Doxed Home Stack Launcher
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-launcher.ps1"
pause
