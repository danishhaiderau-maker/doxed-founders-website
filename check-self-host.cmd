@echo off
cd /d "%~dp0"
echo.
echo === Self-host diagnostics ===
echo.

echo [Port 3000 - Web]
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess"
if errorlevel 1 echo   NOT LISTENING

echo.
echo [Port 4000 - API]
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 4000 -State Listen -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess"
if errorlevel 1 echo   NOT LISTENING

echo.
echo [Files]
if exist .env.self-host (echo   OK  .env.self-host) else (echo   MISSING .env.self-host)
if exist prisma\selfhost.db (echo   OK  prisma\selfhost.db) else (echo   MISSING prisma\selfhost.db)
if exist apps\web\.next\BUILD_ID (echo   OK  production web build) else (echo   MISSING production web build - run build-self-host.cmd)
if exist apps\api\dist\main.js (echo   OK  API dist) else (echo   MISSING API build)

echo.
echo Admin required? NO - for localhost only normal PowerShell is fine.
echo.
pause
