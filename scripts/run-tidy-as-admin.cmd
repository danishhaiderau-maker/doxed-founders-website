@echo off
REM One-shot cleanup script. Right-click -> Run as administrator.
REM This registers the DcfTidyOrphanShells scheduled task (6-hourly elevated
REM cleanup) AND immediately runs the tidy script once to clear current orphans.
REM After this, future orphans are auto-cleaned every 6h without prompting.

setlocal
title Doxed - orphan shell cleanup (one-time admin setup)

cd /d "%~dp0\.."

echo === Registering DcfTidyOrphanShells scheduled task ===
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\register-tidy-orphan-shells.ps1"
if errorlevel 1 goto :error

echo.
echo === Running tidy-orphan-shells.ps1 once now (elevated) ===
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\tidy-orphan-shells.ps1"

echo.
echo === Verifying: current cmd.exe / orphan count ===
powershell -NoProfile -Command "$c = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'cmd.exe' }; $o = $c | Where-Object { [string]::IsNullOrWhiteSpace($_.CommandLine) }; Write-Host ('Total cmd.exe: ' + $c.Count + ' | Orphan: ' + $o.Count)"

echo.
echo === Verifying: critical processes still alive ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*bot.py*' -and $_.CommandLine -like '*7002*' } | Select-Object ProcessId, Name | Format-Table -AutoSize"

echo.
echo Done. Future cleanup runs automatically every 6 hours.
echo Press any key to close this window.
pause >nul
exit /b 0

:error
echo.
echo FAILED. See message above.
pause >nul
exit /b 1
