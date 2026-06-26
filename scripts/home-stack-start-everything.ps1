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
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -Port 7810
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
    $req.Timeout = 2000
    $req.ReadWriteTimeout = 2000
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

$exclude = @($PID)
try {
  $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction SilentlyContinue).ParentProcessId
  if ($parent -gt 0) { $exclude += $parent }
} catch { }
Get-Process cmd, powershell -ErrorAction SilentlyContinue | Where-Object {
  if ($exclude -contains $_.Id) { return $false }
  $t = $_.MainWindowTitle
  return ($t -like "Doxed Start Everything*" -or $t -like "Doxed Stop Everything*")
} | ForEach-Object {
  Stop-ProcessTree $_.Id
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
$botDupes = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like "*btc_conservative_agent*" })
if ($botDupes.Count -gt 1) {
  Write-Step "[1/4] Clearing $($botDupes.Count) duplicate bot processes..."
  Stop-PythonMatching "btc_conservative_agent" | Out-Null
  Stop-ListenPortFast $BotPort | Out-Null
  Start-Sleep -Seconds 2
}
if (-not (Test-BotHealthy)) {
  if (Test-BotHung) {
    Write-Step "[1/4] Clearing hung bot on :$BotPort..."
    Stop-PythonMatching "btc_conservative_agent" | Out-Null
    Stop-ListenPortFast $BotPort | Out-Null
    Start-Sleep -Seconds 2
  }
  Write-Step "[1/4] Opening bot console (Doxed Bot :$BotPort)..."
  Start-VisibleConsole (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -Title "Doxed Bot :$BotPort"
  $messages.Add("[1/4] Bot window opened on :$BotPort")
  Start-Sleep -Seconds 12
} else {
  Write-Step "[1/4] Bot already healthy on :$BotPort"
  $messages.Add("[1/4] Bot already healthy on :$BotPort")
}

# Step 2 — analyzer
if (-not (Test-AnalyzerHealthy)) {
  if (Test-AnalyzerHung) {
    Write-Step "[2/4] Clearing hung analyzer on :$AnalyzerPort..."
    Stop-PythonMatching "analyzer_research_engine" | Out-Null
    Stop-ListenPortFast $AnalyzerPort | Out-Null
    Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
  }
  Write-Step "[2/4] Opening analyzer console (Doxed Analyzer :$AnalyzerPort)..."
  Start-VisibleConsole (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Port", "$AnalyzerPort") -Title "Doxed Analyzer :$AnalyzerPort"
  $messages.Add("[2/4] Analyzer window opened on :$AnalyzerPort")
  Start-Sleep -Seconds 12
} else {
  Write-Step "[2/4] Analyzer already healthy on :$AnalyzerPort"
  $messages.Add("[2/4] Analyzer already healthy")
}

# Step 3 — tunnel (named = hidden background; quick = visible console)
$tunnelUrl = if ((Use-NamedTunnel) -and (Test-Path (Join-Path $repoRoot ".home-use-named-tunnel"))) { $stableUrl } else { Get-TunnelUrl }
$tunnelOk = if ($tunnelUrl) { Test-TunnelPublicHealthy $tunnelUrl } else { $false }
$cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0

if ($tunnelOk) {
  Write-Step "[3/4] Tunnel already live: $tunnelUrl"
  $messages.Add("[3/4] Tunnel already live: $tunnelUrl")
} else {
  if ($cfRunning) {
    Write-Step "[3/4] Restarting cloudflared..."
    Stop-Cloudflared | Out-Null
    Start-Sleep -Seconds 2
  }
  if ((Use-NamedTunnel) -and (Test-Path (Join-Path $repoRoot ".home-use-named-tunnel"))) {
    Write-Step "[3/4] Starting named tunnel hidden (stable URL)..."
    & (Join-Path $scriptDir "restart-home-tunnel.ps1") -Port $BotPort -Force -Hidden | Out-Null
    $messages.Add("[3/4] Named tunnel started hidden - $stableUrl")
    Start-Sleep -Seconds 6
  } else {
    Write-Step "[3/4] Opening tunnel console (Doxed Cloudflare Tunnel)..."
    Start-VisibleConsole (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort", "-Force") -Title "Doxed Cloudflare Tunnel"
    $messages.Add("[3/4] Quick tunnel window opened - URL in .home-tunnel-url")
    Start-Sleep -Seconds 8
  }
}

# Step 4 — background helpers (wire + supervisor only; user-facing consoles above)
Write-Step "[4/4] Starting auto-wire + health supervisor (hidden)..."
if (-not (Test-HomeScriptRunning "auto-wire-after-tunnel.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "auto-wire-after-tunnel.ps1") @("-Quiet")
  $messages.Add("Auto-wire (hidden)")
}
if (-not (Test-HomeScriptRunning "home-stack-supervisor.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "home-stack-supervisor.ps1") @("-BotPort", "$BotPort", "-AnalyzerPort", "$AnalyzerPort")
  $messages.Add("Supervisor (hidden, 24/7 health)")
}
if (-not (Test-HomeScriptRunning "relay-state-pusher.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "relay-state-pusher.ps1") @("-BotPort", "$BotPort")
  $messages.Add("Relay state pusher (hidden, 4s → Railway)")
}

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

if (-not $NoWait) { Wait-ForKey }
