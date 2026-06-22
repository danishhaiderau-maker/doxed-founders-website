@echo off
title Finish home stack + cloud sync
cd /d "%~dp0"
call npm run finish:home-production
if errorlevel 1 pause
exit /b %errorlevel%
