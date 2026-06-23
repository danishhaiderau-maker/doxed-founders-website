# Fast HTTP health probes for home stack (no WMI on hot path).
# Dot-source AFTER home-stack-common.ps1 (uses $BotPort, $AnalyzerPort, $Port from scope).
if (-not $BridgePort) { $BridgePort = $Port }
function Test-HttpOk {
  param(
    [string]$Url,
    [int]$TimeoutSec = 4
  )
  if (-not $Url) { return $false }
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return $r.StatusCode -ge 200 -and $r.StatusCode -lt 300
  } catch {
    return $false
  }
}

function Test-BotHealthy {
  if (-not (Test-PortOpen $BotPort)) { return $false }
  return (Test-HttpOk "http://127.0.0.1:$BotPort/api/ping")
}

function Test-AnalyzerHealthy {
  if (-not (Test-PortOpen $AnalyzerPort)) { return $false }
  try {
    $s = Invoke-RestMethod -Uri "http://127.0.0.1:$AnalyzerPort/api/status" -TimeoutSec 5
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
  param(
    [string]$Url,
    [int]$TimeoutSec = 5
  )
  if (-not $Url) { return $false }
  return (Test-HttpOk "$Url/api/ping" $TimeoutSec)
}

function Test-BotHung {
  return ((Test-PortOpen $BotPort) -and -not (Test-BotHealthy))
}

function Test-AnalyzerHung {
  if (Test-AnalyzerHealthy) { return $false }
  return (Test-PortOpen $AnalyzerPort)
}
