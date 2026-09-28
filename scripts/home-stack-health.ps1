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
  if ($expected.StartsWith($actual, [System.StringComparison]::OrdinalIgnoreCase)) {
    return $true
  }

  # HEAD may advance for tests, docs, runtime ledgers, or Founder OS memory
  # while the executable bot source is unchanged. Restarting a healthy bot for
  # those commits caused a repeating :7002 outage. Only request a replacement
  # when files that actually define the bot runtime differ.
  $runtimePaths = @(
    "services/btc-conservative-agent/bot.py",
    "services/btc-conservative-agent/btc_conservative_agent.py",
    "services/btc-conservative-agent/process_singleton.py",
    "services/btc-conservative-agent/combo_pathway_config.py",
    "services/btc-signal-engine/engine.py",
    "services/btc-signal-engine/manifest.json"
  )
  try {
    & git -C $repoRoot cat-file -e "$actual^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    & git -C $repoRoot diff --quiet "$actual..$expected" -- @runtimePaths 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

function Get-BotRuntimeStatus {
  $status = [pscustomobject]@{
    Responding      = $false
    RevisionMatches = $false
    StateKnown      = $false
    Flat            = $false
    Orders          = -1
    Positions       = -1
    SourceGitRev    = ""
  }
  if (-not (Test-PortOpen $BotPort)) { return $status }

  foreach ($timeoutSec in @(12, 15)) {
    try {
      $ping = Invoke-RestMethod -Uri "http://127.0.0.1:$BotPort/api/ping" -TimeoutSec $timeoutSec
      if ($ping.ok -and $ping.dashboard_owner -eq $true) {
        $status.Responding = $true
        $status.RevisionMatches = Test-BotRevisionMatches $ping
        $status.SourceGitRev = [string]$ping.source_git_rev
        break
      }
    } catch { }
    Start-Sleep -Milliseconds 800
  }

  # A revision mismatch is an upgrade request, not a liveness failure. Prove
  # the source paper book is flat before allowing an automatic replacement.
  # Unknown state fails closed and leaves the currently responding bot alone.
  if ($status.Responding -and -not $status.RevisionMatches) {
    try {
      $state = Invoke-RestMethod -Uri "http://127.0.0.1:$BotPort/api/state" -TimeoutSec 12
      $status.Orders = @($state.orders).Count
      $status.Positions = @($state.positions).Count
      $status.StateKnown = $true
      $status.Flat = ($status.Orders -eq 0 -and $status.Positions -eq 0)
    } catch { }
  }

  return $status
}

function Test-BotHealthy {
  $status = Get-BotRuntimeStatus
  return ($status.Responding -and $status.RevisionMatches)
}

function Test-AnalyzerHealthyQuick {
  if (-not (Test-PortOpen $AnalyzerPort)) { return $false }
  return (Test-HttpOk "http://127.0.0.1:$AnalyzerPort/api/health" 2)
}

function Get-AnalyzerRuntimeStatus {
  $result = [ordered]@{
    Alive = $false
    Ready = $false
    Responding = $false
    CanonicalReportRoot = $false
    RuntimeSync = $false
    Status = $null
  }
  $analyzerPidFile = Join-Path $repoRoot ".home-analyzer.pid"
  if (-not (Test-Path -LiteralPath $analyzerPidFile)) { return [pscustomobject]$result }
  try {
    $analyzerPid = [int]((Get-Content -LiteralPath $analyzerPidFile -Raw -ErrorAction Stop).Trim())
    if ($analyzerPid -le 0) { return [pscustomobject]$result }
  } catch {
    return [pscustomobject]$result
  }
  if (-not (Test-PortOpen $AnalyzerPort)) { return [pscustomobject]$result }
  try {
    $s = Invoke-RestMethod -Uri "http://127.0.0.1:$AnalyzerPort/api/status" -TimeoutSec 12
    $result.Responding = $true
    $result.Status = $s
    # Serve one canonical report tree from the agent data root. The analyzer
    # source lives in research\, but BTC_AGENT_REPORT_DIR intentionally points
    # at $agentDir where the current report manifest and exports are written.
    # A matching version label from any copied checkout must not pass.
    $reportRoot = [string]$s.report_root
    if (-not $reportRoot) { $reportRoot = [string]$s.cwd }
    $expectedReportRoot = [System.IO.Path]::GetFullPath($agentDir).TrimEnd('\', '/')
    try { $actualReportRoot = [System.IO.Path]::GetFullPath($reportRoot).TrimEnd('\', '/') } catch { return [pscustomobject]$result }
    $result.CanonicalReportRoot = $actualReportRoot.Equals($expectedReportRoot, [System.StringComparison]::OrdinalIgnoreCase)
    if (-not $result.CanonicalReportRoot) { return [pscustomobject]$result }
    $result.RuntimeSync = (
      $s.runtime_sync_match -eq $true -and
      [string]$s.runtime_analyzer_sync_id -eq [string]$s.expected_analyzer_sync_id
    )
    if (-not $result.RuntimeSync) { return [pscustomobject]$result }

    # Liveness deliberately ignores report-generation revision/epoch parity.
    # A responsive canonical dashboard must survive mirror sync and analysis;
    # readiness below remains fail closed until that generation is current.
    $result.Alive = ($s.alive -eq $true)
    if (-not $result.Alive) { return [pscustomobject]$result }

    # A completed current-version report is ideal.  Immediately after Start,
    # allow a current-version pass that is actively replacing an older
    # manifest.  /api/status limits this grace to 45 minutes.
    $result.Ready = (
      $s.ok -eq $true -and
      $s.ready -eq $true -and
      (
        $s.report_sync_match -eq $true -or
        ($s.report_sync_pending -eq $true -and $s.analysis_in_progress -eq $true)
      )
    )
    return [pscustomobject]$result
  } catch {
    return [pscustomobject]$result
  }
}

function Test-AnalyzerAlive {
  return [bool](Get-AnalyzerRuntimeStatus).Alive
}

function Test-AnalyzerHealthy {
  # Historical name retained for callers that mean qualification readiness.
  return [bool](Get-AnalyzerRuntimeStatus).Ready
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
  # Stale/mismatched/in-progress is not hung when the canonical API responds.
  if (Test-AnalyzerAlive) { return $false }
  return (Test-PortBound $AnalyzerPort)
}
