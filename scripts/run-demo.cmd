@echo off
REM End-to-end demo harness — one-click Windows launcher.
REM Runs `node scripts/demo-harness.mjs` with all args forwarded.
REM
REM Usage:
REM   scripts\run-demo.cmd               # full demo, replay mode
REM   scripts\run-demo.cmd --stress      # include stress phase
REM   scripts\run-demo.cmd --capture     # refresh cassettes (DEMO_CAPTURE=1)
REM
REM Env:
REM   DEMO_HARNESS_TOKEN   REQUIRED — shared secret for the internal harness route.
REM   DEMO_API_URL         Optional  (default http://127.0.0.1:4000)
REM   DEMO_BOT_URL         Optional  (default http://127.0.0.1:7002)
REM   BOT_CONTROL_SECRET   Optional  (required only for relay cassette replay)

setlocal

if "%DEMO_HARNESS_TOKEN%"=="" (
  echo.
  echo [run-demo] ERROR: DEMO_HARNESS_TOKEN is not set.
  echo [run-demo]        The orchestrator needs it to call the internal harness route.
  echo [run-demo]        Set it in your shell:
  echo            set DEMO_HARNESS_TOKEN=some-secret
  echo [run-demo]        And set the same value on the API service.
  echo.
  exit /b 2
)

node "%~dp0demo-harness.mjs" %*
exit /b %ERRORLEVEL%
