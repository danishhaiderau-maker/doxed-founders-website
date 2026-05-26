@echo off
cd /d "%~dp0"
echo.
echo === Dev mode (easiest - no production build needed) ===
echo Keep this window OPEN while browsing.
echo.
call npm.cmd run dev:ensure
pause
