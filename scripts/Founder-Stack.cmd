@echo off
REM Founder Stack - starts Founder Node tray (if needed) then Founder IDE.
REM If IDE looks black/empty: Founder-Stack.cmd -RestartIde
REM Rare GPU issues: Founder-Stack.cmd -RestartIde -DisableGpu
set "SCRIPT=%~dp0launch-founder-stack.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
