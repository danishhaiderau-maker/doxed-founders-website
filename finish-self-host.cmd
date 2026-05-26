@echo off
setlocal
cd /d "%~dp0"

set LOG=debug-acf3ea.log
echo {"sessionId":"acf3ea","location":"finish-self-host.cmd","message":"start","timestamp":%TIME%}>> "%LOG%"

echo.
echo === Finish self-host database ===
echo.

where node >nul 2>&1
if errorlevel 1 (
  echo ERROR: Node.js not found.
  pause
  exit /b 1
)

call scripts\stop-dev-for-setup.cmd

set DATABASE_URL=file:./prisma/selfhost.db
set PRISMA_SCHEMA=prisma/schema.sqlite.prisma
set DEV_DB=sqlite

del /f /q node_modules\.prisma\client\query_engine-windows.dll.node.tmp* 2>nul

echo [1/3] prisma generate...
call npx.cmd prisma generate --schema prisma/schema.sqlite.prisma
if errorlevel 1 (
  echo.
  echo prisma generate failed — trying dev.db copy fallback...
  if exist prisma\dev.db (
    copy /Y prisma\dev.db prisma\selfhost.db >nul
    echo Copied prisma\dev.db -^> prisma\selfhost.db
    echo {"sessionId":"acf3ea","location":"finish-self-host.cmd","message":"fallback copy dev.db","data":{"ok":true}}>> "%LOG%"
    goto seedskip
  )
  echo {"sessionId":"acf3ea","location":"finish-self-host.cmd","message":"generate failed no fallback","data":{"ok":false}}>> "%LOG%"
  goto fail
)

echo [2/3] prisma db push...
call npx.cmd prisma db push --schema prisma/schema.sqlite.prisma
if errorlevel 1 goto fail

:seedskip
if not exist prisma\selfhost.db (
  echo ERROR: prisma\selfhost.db still missing.
  goto fail
)

echo [3/3] seed database...
call npx.cmd tsx prisma/seed.ts
if errorlevel 1 (
  echo WARN: seed failed — database file exists, you may still start.
)

echo.
echo OK — prisma\selfhost.db is ready.
echo {"sessionId":"acf3ea","location":"finish-self-host.cmd","message":"complete","data":{"ok":true}}>> "%LOG%"
echo Next: npm.cmd run start:self-host
echo.
pause
exit /b 0

:fail
echo.
echo FAILED — copy this window text into chat.
echo {"sessionId":"acf3ea","location":"finish-self-host.cmd","message":"failed","data":{"ok":false}}>> "%LOG%"
pause
exit /b 1
