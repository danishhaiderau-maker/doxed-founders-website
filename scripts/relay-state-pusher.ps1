# Push home bot /api/relay-state to Railway cache every 2 seconds.
# Bot remains authoritative; Railway serves cache-first for Agent Hub.
param(
  [int]$BotPort = 0,
  [int]$IntervalSec = 2,
  [string]$ApiUrl = "https://doxed-founders-website-production.up.railway.app"
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
if (Test-Path -LiteralPath (Join-Path $repoRoot "config\fly-canonical.lock.json")) {
  # Fly publishes the authoritative relay state directly. A desktop pusher
  # would create a second, contradictory state stream.
  exit 0
}
. (Join-Path $scriptDir "home-stack-mode.ps1")
$stackMode = Get-HomeStackMode
if ($BotPort -le 0) { $BotPort = $stackMode.BotPort }

$pusherLock = Join-Path $repoRoot ".home-relay-pusher.lock"
$pusherPidFile = Join-Path $repoRoot ".home-relay-pusher.pid"
$pusherHeartbeatFile = Join-Path $repoRoot ".home-relay-pusher.heartbeat"
$pusherSuccessFile = Join-Path $repoRoot ".home-relay-pusher.success"
try {
  $script:RelayLockHandle = [System.IO.File]::Open($pusherLock, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-Host "relay-state-pusher: another instance is running - exit"
  exit 0
}
Set-Content -LiteralPath $pusherPidFile -Value "$PID" -NoNewline -Encoding UTF8
Set-Content -LiteralPath $pusherHeartbeatFile -Value (Get-Date -Format o) -NoNewline -Encoding UTF8

function Read-DotEnv([string]$Path) {
  $map = @{}
  if (-not (Test-Path $Path)) { return $map }
  Get-Content $Path -ErrorAction SilentlyContinue | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim()
    if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
      $v = $v.Substring(1, $v.Length - 2)
    }
    if ($k) { $map[$k] = $v }
  }
  return $map
}

$vault = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault"
$envFiles = @(
  (Join-Path $vault ".env.vercel.check"),
  (Join-Path $vault ".env.prod.rotate"),
  (Join-Path $repoRoot "apps\api\.env.local")
)
$secret = $null
foreach ($f in $envFiles) {
  $dot = Read-DotEnv $f
  if ($dot["BOT_CONTROL_SECRET"]) {
    $secret = $dot["BOT_CONTROL_SECRET"].Trim()
    break
  }
}
if (-not $secret) {
  Write-Host "relay-state-pusher: BOT_CONTROL_SECRET not found - exiting"
  exit 1
}

# Windows PowerShell's Invoke-RestMethod follows the system proxy/TLS path,
# which intermittently wedged for 30-90 seconds on this host while curl and
# direct HTTPS were healthy. Keep one direct no-proxy HttpClient instead.
Add-Type -AssemblyName System.Net.Http
$httpHandler = New-Object System.Net.Http.HttpClientHandler
$httpHandler.UseProxy = $false
$httpClient = New-Object System.Net.Http.HttpClient($httpHandler)
$httpClient.Timeout = [TimeSpan]::FromSeconds(8)
$httpClient.DefaultRequestHeaders.Add("X-Bot-Control-Secret", $secret)

$logFile = Join-Path $repoRoot ".home-relay-pusher.log"
# A process-local counter restarting at 1 caused Railway to reject every new
# snapshot after a publisher restart whenever the stored sequence was larger.
# Unix milliseconds are monotonic across restarts and safely exact in JSON.
[long]$seq = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$botBase = "http://127.0.0.1:$BotPort"
$pushUrl = ($ApiUrl.TrimEnd("/") + "/api/internal/showcase-snapshot")

function Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
  try {
    if ((Get-Item $logFile -ErrorAction SilentlyContinue).Length -gt 512000) {
      $tail = Get-Content $logFile -Tail 100 -ErrorAction SilentlyContinue
      if ($tail) { $tail | Set-Content $logFile -Encoding UTF8 }
    }
  } catch { }
}

Log "relay-state-pusher started bot=$botBase api=$pushUrl interval=${IntervalSec}s"

while ($true) {
  # The stack supervisor uses this native file heartbeat plus the exact PID
  # marker to distinguish a healthy pusher from a dead/stuck process without
  # enumerating Win32_Process (which can wedge on this host).
  Set-Content -LiteralPath $pusherHeartbeatFile -Value (Get-Date -Format o) -NoNewline -Encoding UTF8
  try {
    # This endpoint is local and cache-backed. One wedged read must not freeze
    # the publisher for 90 seconds while production's cache ages out.
    $resp = Invoke-RestMethod -Uri "$botBase/api/relay-state" -TimeoutSec 5 -Headers @{ Accept = "application/json" }
    if ($resp) {
      $nowSeq = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
      $seq = [Math]::Max(($seq + 1), $nowSeq)
      $body = @{
        snapshot_seq = $seq
        snapshot     = $resp
        bot_version  = $resp.bot_version
        server_ts    = $resp.server_ts
      } | ConvertTo-Json -Compress -Depth 12
      $httpResponse = $null
      $content = New-Object System.Net.Http.StringContent(
        $body,
        [System.Text.Encoding]::UTF8,
        "application/json"
      )
      try {
        $httpResponse = $httpClient.PostAsync($pushUrl, $content).GetAwaiter().GetResult()
        $ackText = $httpResponse.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $httpResponse.IsSuccessStatusCode) {
          throw "snapshot push HTTP $([int]$httpResponse.StatusCode): $ackText"
        }
        $ack = if ($ackText) { $ackText | ConvertFrom-Json } else { @{} }
      } finally {
        if ($httpResponse) { $httpResponse.Dispose() }
        $content.Dispose()
      }
      if ($ack.skipped -eq $true) {
        Log "push rejected as stale seq=$seq stored=$($ack.snapshot_seq)"
      } else {
        Set-Content -LiteralPath $pusherSuccessFile -Value (Get-Date -Format o) -NoNewline -Encoding UTF8
      }
      if (($seq % 30) -eq 0) {
        Log "push ok seq=$seq bot=$($resp.bot_version)"
      }
    }
  } catch {
    Log ('push error: ' + $_.Exception.Message)
  }
  Start-Sleep -Seconds $IntervalSec
}
