# Fast HTTP health probes for home stack (no WMI on hot path).
# Dot-source AFTER home-stack-common.ps1 (uses $BotPort, $AnalyzerPort, $BridgePort from scope).
if (-not $BridgePort) { $BridgePort = 7810 }
function Test-HttpOk {
  param(
    [string]$Url,
    [int]$TimeoutSec = 12
  )
  if (-not $Url) { return $false }
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return $r.StatusCode -ge 200 -and $r.StatusCode -lt 300
  } catch {
    return $false
  }
}

function Test-BotHealthyQuick {
  # Bridge /status must stay under ~2s — single-threaded HttpListener blocks all buttons.
  if (-not (Test-PortOpen $BotPort)) { return $false }
  return (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 2)
}

function Test-BotHealthy {
  if (-not (Test-PortOpen $BotPort)) { return $false }
  if (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 12) { return $true }
  # One retry — slow laptops often miss the first probe under load.
  Start-Sleep -Milliseconds 800
  return (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 15)
}

function Test-AnalyzerHealthyQuick {
  if (-not (Test-PortOpen $AnalyzerPort)) { return $false }
  return (Test-HttpOk "http://127.0.0.1:$AnalyzerPort/api/status" 2)
}

function Test-AnalyzerHealthy {
  if (-not (Test-PortOpen $AnalyzerPort)) { return $false }
  try {
    $s = Invoke-RestMethod -Uri "http://127.0.0.1:$AnalyzerPort/api/status" -TimeoutSec 12
    if (-not $s.ok) { return $false }
    if ($s.generated_at) { return $true }
    if ($s.analyzer_sync_match -eq $true) { return $true }
    $manifestMtime = $null
    if ($s.last_files -and $s.last_files.manifest) {
      $manifestMtime = $s.last_files.manifest
    }
    if ($manifestMtime) { return $true }
    # Stale dashboard-only listener at agent root (no manifest, wrong cwd).
    $cwd = [string]$s.cwd
    if ($cwd -and $cwd -notmatch '[\\/]research$') { return $false }
    return $false
  } catch {
    return $false
  }
}

function Test-BridgeHealthy {
  return (Test-HttpOk "http://127.0.0.1:$BridgePort/health" 3)
}

function Test-TunnelPublicHealthy {
  # F4c-429 (2026-07-08 incident) — Treat HTTP 429 (Cloudflare edge rate
  # limit) as HEALTHY, not dead. The flap loop: this function returned
  # false on 429, the supervisor counted fails, and after 5 ticks it
  # RECOVER'd the tunnel by killing cloudflared. Each kill+restart
  # hammered the edge harder, deepening the rate limit. 429 means
  # "tunnel works, you're asking too often" — so we treat it as alive
  # and signal the shared backoff via Set-TunnelBackoff. Real outages
  # (5xx, conn refused, timeout) still return false and trigger RECOVER.
  param(
    [string]$Url,
    [int]$TimeoutSec = 5
  )
  if (-not $Url) { return $false }
  # If a previous probe saw 429 within the backoff window, skip the
  # network call entirely — we'd just get another 429 and burn the limit
  # further. Trust the prior reading (healthy, rate-limited).
  if (Test-TunnelBackoffActive) { return $true }
  $r = Test-TunnelHttpSmart -Url $Url -TimeoutSec $TimeoutSec
  if ($r.RateLimited) {
    Set-TunnelBackoff -Seconds 180
    return $true
  }
  return [bool]$r.Healthy
}

function Test-RailwayApiHealthy {
  param([int]$TimeoutSec = 8)
  return (Test-HttpOk "https://doxed-founders-website-production.up.railway.app/api/health/live" $TimeoutSec)
}

function Test-ProductionSiteApiHealthy {
  param([int]$TimeoutSec = 10)
  if (-not (Test-HttpOk "https://doxxedcrypto.digital/api/health" $TimeoutSec)) { return $false }
  try {
    $h = Invoke-RestMethod -Uri "https://doxxedcrypto.digital/api/health" -TimeoutSec $TimeoutSec
    return ($h.services.database -eq "ok" -and $h.services.api -eq "ok")
  } catch {
    return $false
  }
}

function Test-BotHung {
  # Port open but /api/ping dead after generous retries — true hung listener.
  if (-not (Test-PortOpen $BotPort)) { return $false }
  if (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 20) { return $false }
  Start-Sleep -Seconds 2
  return -not (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 20)
}

function Test-AnalyzerHung {
  if (Test-AnalyzerHealthy) { return $false }
  return (Test-PortOpen $AnalyzerPort)
}
