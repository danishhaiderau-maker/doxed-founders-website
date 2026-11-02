# Day-1 local paper bot after Fly doxed-btc-bot delete (2026-11-03).
#
# PAPER ONLY. CTRL + GS-01. Live OFF. No Bitfinex trade keys. Disarmed.
# R12: do NOT run on Danish's laptop until the R12 card has RK PASS.
#
# Usage (after RK PASS on card-r12-laptop-local-fresh-start-20261103.md):
#   powershell -ExecutionPolicy Bypass -File scripts\start-local-paper-bot.ps1 -NoWait
#
# Requires exact confirmation:
#   LOCAL_FRESH_START=1
#   LOCAL_FRESH_START_CONFIRM=I_OWN_LOCAL_PAPER_AFTER_FLY_DELETE
param(
  [int]$Port = 7002,
  [string]$VaultEnv = "",
  [string]$DataDir = "",
  [switch]$NoWait,
  [switch]$CollectorOnly
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$logsDir = Join-Path $repoRoot "logs"

# Refuse OneDrive checkouts (AGENTS.md / R12 card).
if ($repoRoot -match '(?i)OneDrive') {
  Write-Host "REFUSED: checkout path is under OneDrive. Use C:\DoxxedCrypto\btc-v31-current" -ForegroundColor Red
  exit 78
}

# Explicit owner confirmation — never implied by vault alone.
$confirm = [Environment]::GetEnvironmentVariable("LOCAL_FRESH_START_CONFIRM", "Process")
if (-not $confirm) {
  $confirm = [Environment]::GetEnvironmentVariable("LOCAL_FRESH_START_CONFIRM", "User")
}
if ($confirm -cne "I_OWN_LOCAL_PAPER_AFTER_FLY_DELETE") {
  Write-Host "REFUSED: set LOCAL_FRESH_START_CONFIRM=I_OWN_LOCAL_PAPER_AFTER_FLY_DELETE (exact)." -ForegroundColor Red
  Write-Host "R12: laptop start waits on RK PASS of the local-fresh R12 card." -ForegroundColor Yellow
  exit 78
}

if (-not (Test-Path $logsDir)) {
  New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

# Load vault NAMES only path (values never printed). Default home-bot.env sibling.
if (-not $VaultEnv) {
  $VaultEnv = Join-Path $repoRoot "home-bot.env"
  if (-not (Test-Path -LiteralPath $VaultEnv)) {
    $VaultEnv = Join-Path (Split-Path $repoRoot -Parent) "doxed-secrets\home-bot.env"
  }
}
if (Test-Path -LiteralPath $VaultEnv) {
  Get-Content -LiteralPath $VaultEnv | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $eq = $line.IndexOf("=")
    if ($eq -lt 1) { return }
    $name = $line.Substring(0, $eq).Trim()
    $value = $line.Substring($eq + 1).Trim()
    if ($value.StartsWith('"') -and $value.EndsWith('"')) {
      $value = $value.Substring(1, $value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
  }
} else {
  Write-Warning "Vault env file not found (names-only expected): will rely on process env."
}

# Refuse trade / read Bitfinex private keys (names only).
foreach ($forbidden in @(
  "BITFINEX_API_KEY",
  "BITFINEX_API_SECRET",
  "BITFINEX_READ_API_KEY",
  "BITFINEX_READ_API_SECRET"
)) {
  $present = [Environment]::GetEnvironmentVariable($forbidden, "Process")
  if ($present) {
    Write-Host "REFUSED: $forbidden must be ABSENT for local paper soak (R10: value not shown)." -ForegroundColor Red
    exit 78
  }
}

$env:LOCAL_FRESH_START = "1"
$env:LOCAL_FRESH_START_CONFIRM = "I_OWN_LOCAL_PAPER_AFTER_FLY_DELETE"
$env:FORCE_PAPER_MODE = "1"
$env:BITFINEX_LIVE_ENABLED = "0"
# HM + Strategist / RK t781u: self-contained — no Railway/Neon coupling.
$env:DISABLE_RAILWAY_NEON_COUPLING = "1"
$env:LOCAL_SELF_CONTAINED = "1"
$env:RELAY_STACK_MODE = "research_only"
if ($env:CREDENTIALS_FROM -and $env:CREDENTIALS_FROM -match '(?i)railway|neon|push-showcase|admin') {
  $env:CREDENTIALS_FROM = "local_env"
}
Remove-Item Env:RAILWAY_ENVIRONMENT -ErrorAction SilentlyContinue
Remove-Item Env:RAILWAY_PROJECT_ID -ErrorAction SilentlyContinue
Remove-Item Env:RAILWAY_API_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:RAILWAY_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:SHOWCASE_RELAY_WEBHOOK_URL -ErrorAction SilentlyContinue
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:NEON_API_KEY -ErrorAction SilentlyContinue
$env:PORT = "$Port"
$env:DASHBOARD_PORT = "$Port"
$env:DASHBOARD_BIND_HOST = "127.0.0.1"
$env:LOCAL_FRESH_HEALTH_JSON = (Join-Path $logsDir "local_fresh_health.json")
$gateReceipt = Join-Path $logsDir "local_bitfinex_live_policy_gate.json"
$env:LOCAL_BITFINEX_LIVE_POLICY_GATE_RECEIPT = $gateReceipt
Remove-Item Env:FLY_APP_NAME -ErrorAction SilentlyContinue
Remove-Item Env:FLY_APP -ErrorAction SilentlyContinue
Remove-Item Env:FLY_MACHINE_ID -ErrorAction SilentlyContinue
Remove-Item Env:FLY_ALLOC_ID -ErrorAction SilentlyContinue
Remove-Item Env:FLY_REGION -ErrorAction SilentlyContinue
Remove-Item Env:FLY_API_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:LIVE_COPY_ARMED -ErrorAction SilentlyContinue
Remove-Item Env:RELAY_ARMED -ErrorAction SilentlyContinue
Remove-Item Env:LIVE_ARMED -ErrorAction SilentlyContinue
Remove-Item Env:BITFINEX_LIVE_ARMED -ErrorAction SilentlyContinue

if ($DataDir) {
  $env:BOT_DATA_DIR = $DataDir
} elseif (-not $env:BOT_DATA_DIR) {
  $env:BOT_DATA_DIR = Join-Path $agentDir "local-fresh-data"
}
if (-not (Test-Path -LiteralPath $env:BOT_DATA_DIR)) {
  New-Item -ItemType Directory -Path $env:BOT_DATA_DIR -Force | Out-Null
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) { $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $pythonCmd) { throw "Python not found on PATH" }
$python = $pythonCmd.Source

# HM AMBER: laptop-side Bitfinex/live policy gate (replaces disabled
# .github/workflows/bitfinex-production-gate.yml). Fail closed before any bot start.
$gateScript = Join-Path $scriptDir "local-bitfinex-live-policy-gate.py"
Write-Host "Running laptop Bitfinex/live policy gate (HM AMBER)..."
& $python $gateScript --apply-defaults --receipt $gateReceipt
if ($LASTEXITCODE -ne 0) {
  Write-Host "REFUSED: Bitfinex/live policy gate failed (receipt $gateReceipt)." -ForegroundColor Red
  exit 78
}
Write-Host "OK policy gate receipt: $gateReceipt"

Set-Location $agentDir
$env:BOT_STARTUP_STDOUT_LOG = Join-Path $logsDir "local-paper-bot.stdout.log"
$env:BOT_STARTUP_STDERR_LOG = Join-Path $logsDir "local-paper-bot.stderr.log"

if ($CollectorOnly) {
  Write-Host "Starting LOCAL public WS collector only (paper engine not started)..."
  $argsList = @(
    "local_public_ws_collector.py",
    "--data-dir", $env:BOT_DATA_DIR,
    "--health-json", $env:LOCAL_FRESH_HEALTH_JSON
  )
  if ($NoWait) {
    $proc = Start-Process -FilePath $python -ArgumentList $argsList `
      -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
    Set-Content -Path (Join-Path $repoRoot ".local-fresh-collector.pid") -Value "$($proc.Id)" -NoNewline
    Write-Host "Collector PID $($proc.Id); health $($env:LOCAL_FRESH_HEALTH_JSON)"
    exit 0
  }
  & $python @argsList
  exit $LASTEXITCODE
}

Write-Host "Starting LOCAL paper bot on :$Port (CTRL+GS-01, FORCE_PAPER, disarmed)..."
Write-Host "Data dir: $($env:BOT_DATA_DIR)"
Write-Host "Health JSON: $($env:LOCAL_FRESH_HEALTH_JSON)"
Write-Host "Dashboard: http://127.0.0.1:$Port/health"

if ($NoWait) {
  $proc = Start-Process -FilePath $python -ArgumentList @("btc_conservative_agent.py") `
    -WorkingDirectory $agentDir -WindowStyle Hidden -PassThru
  Set-Content -Path (Join-Path $repoRoot ".local-fresh-bot.pid") -Value "$($proc.Id)" -NoNewline
  Write-Host "Bot PID $($proc.Id)"
  exit 0
}

& $python "btc_conservative_agent.py"
exit $LASTEXITCODE
