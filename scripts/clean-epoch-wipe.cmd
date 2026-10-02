@echo off
rem clean-epoch-wipe: DRY-RUN plan by default (see clean-epoch-wipe.ps1 for the execute form).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0clean-epoch-wipe.ps1" %*
exit /b %ERRORLEVEL%
