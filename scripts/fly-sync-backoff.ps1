function Get-FlySyncFailureBackoffSeconds {
  param(
    [Parameter(Mandatory = $true)][int]$ConsecutiveFailures,
    [Parameter(Mandatory = $true)][int]$NormalPollSeconds,
    [int]$MaximumBackoffSeconds = 1800
  )
  $normal = [Math]::Max(15, $NormalPollSeconds)
  $cap = [Math]::Max($normal, $MaximumBackoffSeconds)
  if ($ConsecutiveFailures -le 0) { return $normal }
  # Clamp the exponent before shifting so an extremely long outage cannot
  # overflow an integer. Sixteen doublings already exceeds the 30m cap for
  # every supported poll cadence.
  $exponent = [Math]::Min(16, $ConsecutiveFailures - 1)
  $candidate = [int64]$normal * ([int64]1 -shl $exponent)
  return [int][Math]::Min([int64]$cap, $candidate)
}

function Test-FlySyncResourcePressureMessage {
  param([string]$Message = "")
  return [bool]($Message -match '(?i)(?:HTTP\s+|\()(?:502|503)\)?|boot(?:ing)?|starting|restoring|server unavailable|bad gateway|timed?\s*out|timeout|task was canceled|operation was canceled')
}

function Get-FlySyncNextPressureFailureCount {
  param(
    [int]$CurrentCount,
    [bool]$IsResourcePressure
  )
  if (-not $IsResourcePressure) { return 0 }
  return [Math]::Max(0, $CurrentCount) + 1
}
