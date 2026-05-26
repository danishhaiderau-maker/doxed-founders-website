@echo off
setlocal
cd /d "%~dp0"
echo.
echo === DoxedCryptoFounder self-host setup ===
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-self-host.ps1" -NonInteractive -SkipFirewall %*
if errorlevel 1 exit /b 1
echo.
echo Setup complete. Run: start-self-host.cmd
exit /b 0
