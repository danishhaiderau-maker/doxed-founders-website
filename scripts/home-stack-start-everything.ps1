# Visible one-click orchestrator: reload bridge + bot + analyzer + tunnel.
# Opened by Agent Hub "Start everything" — every step uses a console that STAYS OPEN.
param(
  [int]$BotPort = 0,
  [int]$AnalyzerPort = 0,
  [switch]$SkipBridgeRestart
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

$messages = [System.Collections.Generic.List[string]]::new()
$stableUrl = "https://bot.doxxedcrypto.digital"

# Step 0 — reload bridge so Agent Hub buttons use latest scripts.
if (-not $SkipBridgeRestart) {
  Write-Step "[0/4] Reloading command bridge (:7810) with latest scripts..."
  $bridgeScript = Join-Path $scriptDir "ensure-home-bridge.ps1"
  $proc = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $bridgeScript, "-Force", "-Quiet"
  ) -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru -Wait
  if ($proc.ExitCode -ne 0) {
    Write-Host "Bridge reload failed — opening visible bridge window..." -ForegroundColor Red
    Start-VisibleConsole $bridgeScript @("-Force") -Title "Doxed Home Bridge :7810"
    Start-Sleep -Seconds 8
  }
  $deadline = (Get-Date).AddSeconds(40)
  while ((Get-Date) -lt $deadline) {
    if (Test-BridgeHealthyQuick) { break }
    Start-Sleep -Seconds 2
  }
  if (Test-BridgeHealthyQuick) {
    Write-Step "[0/4] Bridge OK on :7810"
    $messages.Add("[0/4] Bridge reloaded")
  } else {
    Write-Host "Bridge still not healthy — check Doxed Home Bridge :7810 window." -ForegroundColor Red
    $messages.Add("[0/4] Bridge reload FAILED — see Doxed Home Bridge window")
  }
} else {
  Write-Step "[0/4] Skipped bridge reload"
}

# Step 1 — bot
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

# Step 3 — tunnel (always visible — never hidden on manual start)
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
  Write-Step "[3/4] Opening tunnel console (Doxed Cloudflare Tunnel)..."
  Start-VisibleConsole (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort", "-Force") -Title "Doxed Cloudflare Tunnel"
  if (Use-NamedTunnel) {
    $messages.Add("[3/4] Named tunnel window opened - $stableUrl")
  } else {
    $messages.Add("[3/4] Quick tunnel window opened - URL in .home-tunnel-url")
  }
  Start-Sleep -Seconds 8
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
Write-Host "Refresh Agent Hub status in 30-60 seconds."

$logLine = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $summary
Add-Content -Path (Join-Path $repoRoot ".home-start-all.log") -Value $logLine

Wait-ForKey
