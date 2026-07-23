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
  if (-not (Test-PortBound $BotPort)) { return $false }
  return (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 2)
}

function Get-ExpectedRepoRevision {
  try {
    $rev = (& git -C $repoRoot rev-parse HEAD 2>$null | Select-Object -First 1)
    if ($LASTEXITCODE -eq 0 -and $rev) { return ([string]$rev).Trim() }
  } catch { }
  try {
    $gitDir = Join-Path $repoRoot ".git"
    $headPath = Join-Path $gitDir "HEAD"
    if (-not (Test-Path -LiteralPath $headPath)) { return $null }
    $head = (Get-Content -LiteralPath $headPath -Raw).Trim()
    if ($head.StartsWith("ref:")) {
      $refName = $head.Substring(4).Trim()
      $refPath = Join-Path $gitDir ($refName -replace '/', '\')
      if (Test-Path -LiteralPath $refPath) {
        return (Get-Content -LiteralPath $refPath -Raw).Trim()
      }
      $packed = Join-Path $gitDir "packed-refs"
      if (Test-Path -LiteralPath $packed) {
        foreach ($line in Get-Content -LiteralPath $packed) {
          if ($line -match '^([0-9a-fA-F]+)\s+(.+)$' -and $matches[2] -eq $refName) {
            return $matches[1]
          }
        }
      }
      return $null
    }
    return $head
  } catch {
    return $null
  }
}

function Test-BotRevisionMatches([object]$Ping) {
  $expected = [string](Get-ExpectedRepoRevision)
  if (-not $expected) { return $true }
  $actual = [string]$Ping.source_git_rev
  if (-not $actual -or $actual -eq "unknown") { return $false }
  return $expected.StartsWith($actual, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-BotHealthy {
  if (-not (Test-PortOpen $BotPort)) { return $false }
  try {
    $ping = Invoke-RestMethod -Uri "http://127.0.0.1:$BotPort/api/ping" -TimeoutSec 12
    if ($ping.ok -and $ping.dashboard_owner -eq $true -and (Test-BotRevisionMatches $ping)) {
      return $true
    }
  } catch { }
  # One retry — slow laptops often miss the first probe under load.
  Start-Sleep -Milliseconds 800
  try {
    $ping = Invoke-RestMethod -Uri "http://127.0.0.1:$BotPort/api/ping" -TimeoutSec 15
    return (
      $ping.ok -and
      $ping.dashboard_owner -eq $true -and
      (Test-BotRevisionMatches $ping)
    )
  } catch {
    return $false
  }
}

function Test-AnalyzerHealthyQuick {
  if (-not (Test-PortOpen $AnalyzerPort)) { return $false }
  return (Test-HttpOk "http://127.0.0.1:$AnalyzerPort/api/health" 2)
}

function Test-AnalyzerHealthy {
  $analyzerPidFile = Join-Path $repoRoot ".home-analyzer.pid"
  if (-not (Test-Path -LiteralPath $analyzerPidFile)) { return $false }
  try {
    $analyzerPid = [int]((Get-Content -LiteralPath $analyzerPidFile -Raw -ErrorAction Stop).Trim())
    $analyzerProcess = Get-Process -Id $analyzerPid -ErrorAction Stop
    if ($analyzerProcess.ProcessName -notin @("python", "pythonw")) { return $false }
  } catch {
    return $false
  }
  if (-not (Test-PortOpen $AnalyzerPort)) { return $false }
  try {
    $s = Invoke-RestMethod -Uri "http://127.0.0.1:$AnalyzerPort/api/status" -TimeoutSec 12
    if (-not $s.ok) { return $false }
    # Serve one canonical report tree from the analyzer's research directory.
    # A matching version label from any copied checkout must not pass.
    $reportRoot = [string]$s.report_root
    if (-not $reportRoot) { $reportRoot = [string]$s.cwd }
    $expectedReportRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "services\btc-conservative-agent\research")).TrimEnd('\', '/')
    try { $actualReportRoot = [System.IO.Path]::GetFullPath($reportRoot).TrimEnd('\', '/') } catch { return $false }
    if (-not $actualReportRoot.Equals($expectedReportRoot, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if ($s.runtime_sync_match -ne $true) { return $false }
    if ([string]$s.runtime_analyzer_sync_id -ne [string]$s.expected_analyzer_sync_id) { return $false }

    # A completed current-version report is ideal.  Immediately after Start,
    # allow a current-version pass that is actively replacing an older
    # manifest.  /api/status limits this grace to 45 minutes.
    if ($s.report_sync_match -eq $true) { return $true }
    if ($s.report_sync_pending -eq $true -and $s.analysis_in_progress -eq $true) { return $true }
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
  if (-not (Test-PortBound $BotPort)) { return $false }
  if (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 20) { return $false }
  Start-Sleep -Seconds 2
  return -not (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping" 20)
}

function Test-AnalyzerHung {
  if (Test-AnalyzerHealthy) { return $false }
  return (Test-PortBound $AnalyzerPort)
}
