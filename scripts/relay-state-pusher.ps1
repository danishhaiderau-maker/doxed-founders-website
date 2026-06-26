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
. (Join-Path $scriptDir "home-stack-mode.ps1")
$stackMode = Get-HomeStackMode
if ($BotPort -le 0) { $BotPort = $stackMode.BotPort }

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

$logFile = Join-Path $repoRoot ".home-relay-pusher.log"
$seq = 0
$botBase = "http://127.0.0.1:$BotPort"
$pushUrl = ($ApiUrl.TrimEnd("/") + "/api/internal/showcase-snapshot")

function Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

Log "relay-state-pusher started bot=$botBase api=$pushUrl interval=${IntervalSec}s"

while ($true) {
  try {
    $resp = Invoke-RestMethod -Uri "$botBase/api/relay-state" -TimeoutSec 90 -Headers @{ Accept = "application/json" }
    if ($resp) {
      $seq += 1
      $body = @{
        snapshot_seq = $seq
        snapshot     = $resp
        bot_version  = $resp.bot_version
        server_ts    = $resp.server_ts
      } | ConvertTo-Json -Compress -Depth 12
      Invoke-RestMethod -Method Post -Uri $pushUrl -TimeoutSec 30 `
        -Headers @{ "Content-Type" = "application/json"; "X-Bot-Control-Secret" = $secret } `
        -Body $body | Out-Null
      if ($seq -eq 1 -or ($seq % 30) -eq 0) {
        Log "push ok seq=$seq bot=$($resp.bot_version)"
      }
    }
  } catch {
    Log ('push error: ' + $_.Exception.Message)
  }
  Start-Sleep -Seconds $IntervalSec
}
