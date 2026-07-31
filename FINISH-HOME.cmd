@echo off
title Recover Fly dashboard and analyzer mirror
cd /d "%~dp0"
call npm run finish:home-production
if errorlevel 1 pause
exit /b %errorlevel%
