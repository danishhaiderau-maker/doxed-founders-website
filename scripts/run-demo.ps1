# End-to-end demo harness — one-click PowerShell launcher.
# Runs `node scripts/demo-harness.mjs` with all args forwarded.
#
# Usage:
#   .\scripts\run-demo.ps1               # full demo, replay mode
#   .\scripts\run-demo.ps1 -Stress       # include stress phase
#   .\scripts\run-demo.ps1 --stress      # args are forwarded verbatim too
#   .\scripts\run-demo.ps1 --capture     # refresh cassettes (DEMO_CAPTURE=1)
#
# Env:
#   DEMO_HARNESS_TOKEN   REQUIRED — shared secret for the internal harness route.
#   DEMO_API_URL         Optional  (default http://127.0.0.1:4000)
#   DEMO_BOT_URL         Optional  (default http://127.0.0.1:7002)
#   BOT_CONTROL_SECRET   Optional  (required only for relay cassette replay)

[CmdletBinding()]
param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$ForwardedArgs
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($env:DEMO_HARNESS_TOKEN)) {
  Write-Host ''
  Write-Host '[run-demo] ERROR: DEMO_HARNESS_TOKEN is not set.' -ForegroundColor Red
  Write-Host '[run-demo]        The orchestrator needs it to call the internal harness route.'
  Write-Host '[run-demo]        Set it in your shell:'
  Write-Host '            $env:DEMO_HARNESS_TOKEN = "some-secret"'
  Write-Host '[run-demo]        And set the same value on the API service.'
  Write-Host ''
  exit 2
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$harness = Join-Path $scriptDir 'demo-harness.mjs'

if ($ForwardedArgs -and $ForwardedArgs.Count -gt 0) {
  & node $harness @ForwardedArgs
} else {
  & node $harness
}
exit $LASTEXITCODE
