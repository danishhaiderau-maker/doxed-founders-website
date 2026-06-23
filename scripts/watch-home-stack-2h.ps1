# 2-hour home stack + Bitfinex relay watch (logs to .home-watch-2h.log)
param(
  [int]$IntervalSec = 300,
  [int]$DurationMin = 120
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logFile = Join-Path $repoRoot ".home-watch-2h.log"
$deadline = (Get-Date).AddMinutes($DurationMin)

function Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line
  Write-Host $line
}

function Test-Port([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $a = $c.BeginConnect("127.0.0.1", $P, $null, $null)
    if (-not $a.AsyncWaitHandle.WaitOne(800)) { $c.Close(); return $false }
    $c.EndConnect($a); $c.Close(); return $true
  } catch { return $false }
}

Log "watch started interval=${IntervalSec}s duration=${DurationMin}m"

while ((Get-Date) -lt $deadline) {
  $ports = @(7810, 7002, 9500, 7800, 9001)
  $portParts = @()
  foreach ($p in $ports) {
    $portParts += ":$p=" + $(if (Test-Port $p) { "UP" } else { "DOWN" })
  }
  $portStr = $portParts -join " "
  $bridge = "?"
  $bfx = "?"
  $ver = "?"
  $tunnel = "?"
  $cf = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0

  try {
    $st = Invoke-RestMethod "http://127.0.0.1:7810/status" -TimeoutSec 4
    $bridge = "bot=$($st.bot.online) an=$($st.analyzer.online) tun=$($st.tunnel.live)"
  } catch { $bridge = "FAIL" }

  try {
    $ping = Invoke-RestMethod "http://127.0.0.1:7002/api/ping" -TimeoutSec 4
    $ver = $ping.bot_version
  } catch { $ver = "bot-down" }

  try {
    $state = Invoke-RestMethod "http://127.0.0.1:7002/api/state" -TimeoutSec 6
    $bfx = "bitfinex_live=$($state.bitfinex_live_enabled) armed=$($state.live_armed)"
  } catch { $bfx = "state-fail" }

  try {
    $tp = Invoke-RestMethod "https://bot.doxxedcrypto.digital/api/ping" -TimeoutSec 8
    $tunnel = "pub=$($tp.ok) v=$($tp.bot_version)"
  } catch { $tunnel = "pub-fail" }

  try {
    $api = Invoke-RestMethod "https://doxxedcrypto.digital/api/health" -TimeoutSec 8
    $apiOk = $api.status
  } catch { $apiOk = "fail" }

  $doxedWin = @(Get-Process cmd -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like "Doxed*" }).Count
  Log "tick $portStr | bridge=$bridge | $bfx | ver=$ver | tunnel=$tunnel cf=$cf | api=$apiOk | doxed_windows=$doxedWin"

  $supLog = Join-Path $repoRoot ".home-stack-supervisor.log"
  if (Test-Path $supLog) {
    $tail = Get-Content $supLog -Tail 1 -ErrorAction SilentlyContinue
    if ($tail) { Log "supervisor: $tail" }
  }

  Start-Sleep -Seconds $IntervalSec
}

Log "watch finished"
