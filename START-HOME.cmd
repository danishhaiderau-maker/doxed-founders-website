@echo off
title Doxed Home Bot Stack
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-home-stack.ps1"
pause
