@echo off
REM Founder Stack ??? starts Founder Node tray (if needed) then Founder IDE.
set "SCRIPT=%~dp0launch-founder-stack.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
