@echo off
title Doxed Home Stack Launcher
cd /d "%~dp0"
echo Bridge :7810 only - bot dashboard stays on :7800
echo If buttons show Done but nothing happens, run RESTART-LAUNCHER.cmd first.
powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-launcher.ps1"
