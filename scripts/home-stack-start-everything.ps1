# Visible one-click orchestrator: reload bridge + bot + analyzer + tunnel.
# Opened by Agent Hub "Start everything" — every step uses a console that STAYS OPEN.
param(
  [int]$BotPort = 0,
  [int]$AnalyzerPort = 0,
  [switch]$SkipBridgeRestart,
  [switch]$NoWait
)

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Doxed Start Everything"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-mode.ps1")
$stackMode = Get-HomeStackMode
if ($BotPort -le 0) { $BotPort = $stackMode.BotPort }
if ($AnalyzerPort -le 0) { $AnalyzerPort = $stackMode.AnalyzerPort }
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -BridgePort 7810
. (Join-Path $scriptDir "home-stack-health.ps1")

function Write-Step([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
  Write-Host $line -ForegroundColor Cyan
  Add-Content -Path (Join-Path $repoRoot ".home-start-all.log") -Value $line -ErrorAction SilentlyContinue
}

function Wait-ForKey {
  Write-Host ""
  Write-Host "--- This window stays open. Press Enter to close ---" -ForegroundColor Yellow
  try { Read-Host } catch { Start-Sleep -Seconds 86400 }
}

function Test-BridgeHealthyQuick {
  try {
    $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:7810/health")
    $req.Method = "GET"
    $req.Timeout = 7000
    $req.ReadWriteTimeout = 7000
    $resp = $req.GetResponse()
    $ok = ($resp.StatusCode -eq 200)
    $resp.Close()
    return $ok
  } catch {
    return $false
  }
}

Write-Host ""
Write-Host "=== Doxed Start Everything ===" -ForegroundColor Green
Write-Host "Global showcase bot :$BotPort | analyzer :$AnalyzerPort | bridge :7810"
Write-Host ""

Clear-HomeStackUserStopped

$startLockPath = Join-Path $repoRoot ".home-start-everything.lock"
$startLockHandle = $null
try {
  $startLockHandle = [System.IO.File]::Open(
    $startLockPath,
    [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
  )
} catch {
  Write-Host "Start Showcase is already running - not launching a duplicate." -ForegroundColor Yellow
  exit 0
}

$messages = [System.Collections.Generic.List[string]]::new()
$stableUrl = "https://bot.doxxedcrypto.digital"

# Step 0 — reload bridge only if unhealthy (avoid killing a working bridge mid-start).
if (-not $SkipBridgeRestart) {
  if (Test-BridgeHealthyQuick) {
    Write-Step "[0/4] Bridge already OK on :7810 (skipping reload)"
    $messages.Add("[0/4] Bridge already OK")
  } else {
    Write-Step "[0/4] Bridge offline - opening bridge window..."
    Start-VisibleConsole (Join-Path $scriptDir "ensure-home-bridge.ps1") @("-Force") -Title "Doxed Home Bridge :7810"
    $deadline = (Get-Date).AddSeconds(40)
    while ((Get-Date) -lt $deadline) {
      if (Test-BridgeHealthyQuick) { break }
      Start-Sleep -Seconds 2
    }
    if (Test-BridgeHealthyQuick) {
      Write-Step "[0/4] Bridge OK on :7810"
      $messages.Add("[0/4] Bridge started")
    } else {
      Write-Host "Bridge still not healthy - check Doxed Home Bridge :7810 window." -ForegroundColor Red
      $messages.Add("[0/4] Bridge start FAILED")
    }
  }
} else {
  Write-Step "[0/4] Skipped bridge reload"
}

# Step 1 — bot (kill duplicates before start)
$botRuntime = Get-BotRuntimeStatus
$botHealthy = ($botRuntime.Responding -and $botRuntime.RevisionMatches)
$botUpgradeDeferred = (
  $botRuntime.Responding -and
  -not $botRuntime.RevisionMatches -and
  (-not $botRuntime.StateKnown -or -not $botRuntime.Flat)
)
if ($botUpgradeDeferred) {
  Write-Step "[1/4] Bot update deferred - source book is active or unavailable (orders=$($botRuntime.Orders), positions=$($botRuntime.Positions))"
  $messages.Add("[1/4] Bot update deferred until source flat - keep relay paused")
} elseif (-not $botHealthy) {
  if (
    $botRuntime.Responding -and
    -not $botRuntime.RevisionMatches -and
    $botRuntime.StateKnown -and
    $botRuntime.Flat
  ) {
    Write-Step "[1/4] Replacing stale bot revision from verified flat source boundary..."
    Stop-BotPidFile | Out-Null
    Stop-ListenPortFast $BotPort | Out-Null
    Start-Sleep -Seconds 2
  } elseif (Test-BotHung) {
    Write-Step "[1/4] Clearing hung bot on :$BotPort..."
    Stop-BotPidFile | Out-Null
    Stop-ListenPortFast $BotPort | Out-Null
    Start-Sleep -Seconds 2
  }
  Write-Step "[1/4] Opening bot console (Doxed Bot :$BotPort)..."
  Start-VisibleConsole (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -Title "Doxed Bot :$BotPort"
  $messages.Add("[1/4] Bot window opened on :$BotPort")
  $botDeadline = (Get-Date).AddSeconds(90)
  while ((Get-Date) -lt $botDeadline) {
    if (Test-BotHealthy) { break }
    Start-Sleep -Seconds 3
  }
  if (Test-BotHealthy) {
    Write-Step "[1/4] Bot verified on :$BotPort (owner + canonical source revision)"
    $messages.Add("[1/4] Bot verified")
  } else {
    Write-Step "[1/4] Bot FAILED canonical verification on :$BotPort"
    $messages.Add("[1/4] Bot verification FAILED - keep relay paused")
  }
} else {
  Write-Step "[1/4] Bot already verified on :$BotPort"
  $messages.Add("[1/4] Bot already verified on :$BotPort")
}

# Step 2 — analyzer
if (-not (Test-AnalyzerHealthy)) {
  if (Test-AnalyzerHung) {
    Write-Step "[2/4] Clearing hung analyzer on :$AnalyzerPort..."
    $analyzerPidFile = Join-Path $repoRoot ".home-analyzer.pid"
    if (Test-Path -LiteralPath $analyzerPidFile) {
      try {
        $analyzerPid = [int](Get-Content -LiteralPath $analyzerPidFile -Raw)
        if ($analyzerPid -gt 0) {
          Stop-Process -Id $analyzerPid -Force -ErrorAction SilentlyContinue
        }
      } catch { }
      Remove-Item -LiteralPath $analyzerPidFile -Force -ErrorAction SilentlyContinue
    }
    Stop-ListenPortFast $AnalyzerPort | Out-Null
    Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
  }
  Write-Step "[2/4] Opening analyzer console (Doxed Analyzer :$AnalyzerPort)..."
  Start-VisibleConsole (Join-Path $scriptDir "start-home-analyzer.ps1") @(
    "-Port", "$AnalyzerPort",
    "-NoWait"
  ) -Title "Doxed Analyzer :$AnalyzerPort"
  $messages.Add("[2/4] Analyzer window opened on :$AnalyzerPort")
  # A full historical research pass can exceed 90s on a cold start. Match the
  # crash monitor grace so the command center does not show a false failure.
  $analyzerDeadline = (Get-Date).AddSeconds(180)
  while ((Get-Date) -lt $analyzerDeadline) {
    if (Test-AnalyzerHealthy) { break }
    Start-Sleep -Seconds 3
  }
  if (Test-AnalyzerHealthy) {
    Write-Step "[2/4] Analyzer verified on :$AnalyzerPort (runtime sync + fresh/current pass)"
    $messages.Add("[2/4] Analyzer verified")
  } else {
    Write-Step "[2/4] Analyzer FAILED verification on :$AnalyzerPort"
    $messages.Add("[2/4] Analyzer verification FAILED - keep relay paused")
  }
} else {
  Write-Step "[2/4] Analyzer already verified on :$AnalyzerPort"
  $messages.Add("[2/4] Analyzer already verified")
}

# Step 3 — tunnel (named = hidden background; quick = visible console)
$tunnelUrl = if (Use-NamedTunnel) { $stableUrl } else { Get-TunnelUrl }
$tunnelProbe = if ($tunnelUrl) {
  Test-TunnelHttpSmart -Url $tunnelUrl -TimeoutSec 6 -UserAgent "dcf-start-everything/1.0"
} else {
  @{ Healthy = $false; StatusCode = 0; RateLimited = $false; Error = "no-url" }
}
$tunnelOk = [bool]$tunnelProbe.Healthy
$cfRunning = Test-TunnelConnectorPresent $tunnelProbe

if ($tunnelOk) {
  Write-Step "[3/4] Tunnel already live: $tunnelUrl"
  $messages.Add("[3/4] Tunnel already live: $tunnelUrl")
} elseif ($cfRunning) {
  Write-Step "[3/4] Tunnel connector is up; waiting for bot origin: $tunnelUrl"
  $messages.Add("[3/4] Tunnel connector preserved while bot origin recovers")
} else {
  if (Use-NamedTunnel) {
    Write-Step "[3/4] Starting named tunnel hidden (stable URL)..."
    Start-HomeTunnel -Port $BotPort -Force
    $messages.Add("[3/4] Named tunnel started hidden - $stableUrl")
    Start-Sleep -Seconds 6
  } else {
    Write-Step "[3/4] Opening quick tunnel console..."
    Start-HomeTunnel -Port $BotPort -Force -PreferVisible
    $messages.Add("[3/4] Quick tunnel console opened - URL in .home-tunnel-url")
    Start-Sleep -Seconds 8
  }
}

# Step 4 — background helpers (wire + supervisor + bridge watchdog; user-facing consoles above)
Write-Step "[4/4] Starting auto-wire + health supervisor + bridge watchdog (hidden)..."
# These helpers are lock-protected. Starting them unconditionally is bounded
# and avoids the Win32_Process command-line provider that can hang this PC.
Start-HiddenPs1 (Join-Path $scriptDir "auto-wire-after-tunnel.ps1") @("-Quiet")
$messages.Add("Auto-wire (hidden)")
Start-HiddenPs1 (Join-Path $scriptDir "home-stack-supervisor.ps1") @("-BotPort", "$BotPort", "-AnalyzerPort", "$AnalyzerPort")
$messages.Add("Supervisor (hidden, 24/7 health)")
Start-HiddenPs1 (Join-Path $scriptDir "relay-state-pusher.ps1") @("-BotPort", "$BotPort")
$messages.Add("Relay state pusher (hidden, 4s → Railway)")
# Bridge :7810 auto-respawn watchdog — survives terminal closure (detached hidden loop).
# Best-effort: also registers the DoxxedBridgeWatch scheduled task when run as admin.
& (Join-Path $scriptDir "register-bridge-watchdog.ps1") -Quiet
$messages.Add("Bridge watchdog (hidden, 10s poll)")

$summary = ($messages -join " | ")
Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
Write-Host $summary
Write-Host ""
Write-Host "Keep these windows OPEN (do not press Enter in bot/analyzer/tunnel unless stopping):"
Write-Host "  • Doxed Home Bridge :7810"
Write-Host "  • Doxed Bot :$BotPort"
Write-Host "  • Doxed Analyzer :$AnalyzerPort"
Write-Host "  • Doxed Cloudflare Tunnel"
Write-Host ""
Write-Host "Refresh Agent Hub status in 30s, 60s, and 90s (/api/ping is fast; full dashboard takes longer)."

$logLine = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $summary
Add-Content -Path (Join-Path $repoRoot ".home-start-all.log") -Value $logLine

$startLockHandle.Dispose()
Remove-Item -LiteralPath $startLockPath -Force -ErrorAction SilentlyContinue
if (-not $NoWait) { Wait-ForKey }
