@echo off
echo Stopping processes on ports 3000 and 4000 (unlocks Prisma DLL)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-dev.ps1"
timeout /t 3 /nobreak >nul
echo Ready for prisma generate.
