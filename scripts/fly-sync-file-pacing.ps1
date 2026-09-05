# Pure pacing policy. This helper does not perform IO or change sync ownership.
# It is not active until a reviewed client imports it at a safe owner boundary.
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
  # Retain the original delay for large reads, slow requests, and ANY elevated
  # pressure state. Fast small objects share a 500ms request-start budget
  # (at most two serial requests/sec), plus a minimum scheduler yield.
  if ($FileBytes -gt 16KB -or $RequestElapsedMs -ge 1000 -or
      $AdaptiveThrottleMs -gt $BaseInterChunkThrottleMs) {
    return [int]$protectedDelay
  }
  return [int][Math]::Max(50, [Math]::Ceiling(500 - $RequestElapsedMs))
}
