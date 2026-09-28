# Pure pacing policy. This helper does not perform IO or change sync ownership.
# Imported by the sync client after the authorized stopped-owner handoff.
$script:FlySyncSmallObjectMaxBytes = 16KB
# A small read that is slow only because the shared Fly VM is answering every
# request slowly is latency, not transfer pressure caused by this client.
# Only escalate for small reads once the latency itself signals distress.
$script:FlySyncSmallObjectDistressMs = 6000
$script:FlySyncLargeObjectSlowMs = 2000

function Test-FlySyncSlowSuccessEscalates {
  param(
    [Parameter(Mandatory = $true)][long]$PayloadBytes,
    [Parameter(Mandatory = $true)][double]$RequestElapsedMs
  )
  if ($PayloadBytes -lt 0 -or [double]::IsNaN($RequestElapsedMs) -or
      [double]::IsInfinity($RequestElapsedMs) -or $RequestElapsedMs -lt 0) {
    throw 'INVALID_SYNC_PACING_OBSERVATION'
  }
  if ($PayloadBytes -le $script:FlySyncSmallObjectMaxBytes) {
    return [bool]($RequestElapsedMs -ge $script:FlySyncSmallObjectDistressMs)
  }
  return [bool]($RequestElapsedMs -ge $script:FlySyncLargeObjectSlowMs)
}

function Get-FlySyncInterFileDelayMs {
  param(
    [Parameter(Mandatory = $true)][long]$FileBytes,
    [Parameter(Mandatory = $true)][double]$RequestElapsedMs,
    [Parameter(Mandatory = $true)][int]$AdaptiveThrottleMs,
    [int]$BaseInterFileThrottleMs = 1500,
    [int]$BaseInterChunkThrottleMs = 1000
  )
  if ($FileBytes -lt 0 -or [double]::IsNaN($RequestElapsedMs) -or
      [double]::IsInfinity($RequestElapsedMs) -or $RequestElapsedMs -lt 0 -or
      $AdaptiveThrottleMs -lt $BaseInterChunkThrottleMs -or
      $BaseInterFileThrottleMs -lt 1 -or $BaseInterChunkThrottleMs -lt 1) {
    throw 'INVALID_SYNC_PACING_OBSERVATION'
  }
  $protectedDelay = [Math]::Max($BaseInterFileThrottleMs, $AdaptiveThrottleMs)
  # Retain the original delay for large reads, distressed requests, and ANY
  # elevated pressure state.
  if ($FileBytes -gt $script:FlySyncSmallObjectMaxBytes -or
      $RequestElapsedMs -ge $script:FlySyncSmallObjectDistressMs -or
      $AdaptiveThrottleMs -gt $BaseInterChunkThrottleMs) {
    return [int]$protectedDelay
  }
  # Fast small objects share a 500ms request-start budget (at most two serial
  # requests/sec). A slower small read already paced itself; only add a fixed
  # scheduler yield so a serial client stays below one request per second.
  if ($RequestElapsedMs -ge 1000) {
    return 250
  }
  return [int][Math]::Max(50, [Math]::Ceiling(500 - $RequestElapsedMs))
}
