@echo off
setlocal
cd /d "%~dp0"

echo.
echo === Build web + API for production self-host ===
echo.

call npm.cmd run build:utils
if errorlevel 1 goto fail

call npx.cmd prisma generate --schema prisma/schema.sqlite.prisma
if errorlevel 1 (
  echo WARN: prisma generate failed — using existing client if present
)

call npm.cmd run build --workspace=@dcf/api
if errorlevel 1 goto fail

call npm.cmd run build --workspace=@dcf/web
if errorlevel 1 goto fail

echo.
echo OK — production builds ready. Run: npm.cmd run start:self-host
pause
exit /b 0

:fail
echo BUILD FAILED
pause
exit /b 1
