@echo off
setlocal
cd /d "%~dp0"
echo.
echo === DoxedCryptoFounder self-host bootstrap ===
echo.
node scripts\bootstrap-self-host.mjs
if errorlevel 1 (
  echo.
  echo FAILED — copy the error above and share it in chat.
  pause
  exit /b 1
)
echo.
echo OK. Next: double-click start-self-host.cmd
pause
exit /b 0
