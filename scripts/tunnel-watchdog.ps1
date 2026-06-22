# Keeps Cloudflare tunnel alive - restarts cloudflared when process dies or public URL stops responding.
# Started automatically by home-stack-launcher "Start everything".
param(
  [int]$BotPort = 7800,
  [int]$IntervalSec = 90,
  [int]$RestartCooldownSec = 300,
  [string]$BridgeUrl = "http://127.0.0.1:7810"
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$logFile = Join-Path $repoRoot ".home-tunnel-watchdog.log"
$namedFlag = Join-Path $repoRoot ".home-use-named-tunnel"
$stableUrl = "https://bot.doxxedcrypto.digital"

function Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
  Write-Host $line
}

function Test-LocalPort([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $async = $c.ConnectAsync("127.0.0.1", $P)
    if (-not $async.Wait(1500)) { return $false }
    $c.Close()
    return $true
  } catch {
    return $false
  }
}

function Read-TunnelUrl {
  $configDir = Join-Path $env:USERPROFILE ".cloudflared"
  $cred = Get-ChildItem -Path (Join-Path $configDir "doxed-btc-bot*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
  if ((Test-Path $namedFlag) -and $cred) { return $stableUrl }
  $token = Join-Path $configDir "doxed-btc-bot.token"
  if ((Test-Path $namedFlag) -and (Test-Path $token)) { return $stableUrl }
  if (-not (Test-Path $tunnelUrlFile)) { return $null }
  $raw = Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue
  if ($null -eq $raw) { return $null }
  $t = "$raw".Trim()
  if (-not $t) { return $null }
  if ($t -match 'bot\.doxxedcrypto\.digital' -and -not (Test-Path $token) -and -not $cred) { return $null }
  return $t
}

function Probe([string]$Url, [int]$TimeoutSec = 12) {
  if (-not $Url) { return $false }
  try {
    $r = Invoke-WebRequest -Uri ($Url + "/api/ping") -UseBasicParsing -TimeoutSec $TimeoutSec
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Invoke-Bridge([string]$Path) {
  try {
    $r = Invoke-WebRequest -Uri ($BridgeUrl + $Path) -UseBasicParsing -TimeoutSec 60
    return $r.Content
  } catch {
    Log ("bridge " + $Path + " failed: " + $_.Exception.Message)
    return $null
  }
}

# Only one watchdog — duplicate instances cause restart storms.
$others = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
  Where-Object {
    $_.CommandLine -and $_.CommandLine -like "*tunnel-watchdog.ps1*" -and $_.ProcessId -ne $PID
  }
foreach ($p in $others) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}

Log ("watchdog started (interval " + $IntervalSec + "s, cooldown " + $RestartCooldownSec + "s, named=" + (Test-Path $namedFlag) + ")")

$lastWireUrl = $null
$lastRestartAt = [datetime]::MinValue

while ($true) {
  $url = Read-TunnelUrl
  $cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $botLocal = Test-LocalPort $BotPort
  $live = if ($botLocal) { Probe $url } else { $false }

  if (-not $botLocal) {
    Log ("bot :$BotPort offline — skip tunnel restart (start bot first)")
  } elseif (-not $cfRunning -or -not $live) {
    $sinceRestart = (Get-Date) - $lastRestartAt
    if ($sinceRestart.TotalSeconds -lt $RestartCooldownSec) {
      Log ("tunnel unhealthy but cooldown ($([int]$sinceRestart.TotalSeconds)s/$RestartCooldownSec) cloudflared=$cfRunning live=$live url=$url)")
    } else {
      Log ("tunnel unhealthy cloudflared=$cfRunning live=$live url=$url - restarting")
      Invoke-Bridge "/cmd/start-tunnel" | Out-Null
      $lastRestartAt = Get-Date
      Start-Sleep -Seconds 25
      $url = Read-TunnelUrl
      $live = Probe $url
      $cfAfter = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
      Log ("after restart cloudflared=$cfAfter live=$live url=$url")
    }
  }

  if ($live -and $url -and $url -ne $lastWireUrl) {
    if ($url -match 'trycloudflare\.com' -or (Test-Path $namedFlag)) {
      Log ("wiring " + $url + " to Neon + Railway")
      $wirePath = "/cmd/wire?url=" + [uri]::EscapeDataString($url)
      Invoke-Bridge $wirePath | Out-Null
      $lastWireUrl = $url
    }
  }

  Start-Sleep -Seconds $IntervalSec
}
