@echo off
title Doxed Home Stack Launcher
cd /d "%~dp0"
echo Bridge :7810 - global showcase bot :7002 / analyzer :9500
echo If buttons show Done but nothing happens, run RESTART-LAUNCHER.cmd first.
powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\home-stack-launcher.ps1"
