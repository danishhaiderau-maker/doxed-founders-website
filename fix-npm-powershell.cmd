@echo off
echo Fixing PowerShell so "npm" works (Current User only)...
powershell -NoProfile -Command "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force"
echo.
echo Done. Close and reopen PowerShell, then npm run ... will work.
pause
