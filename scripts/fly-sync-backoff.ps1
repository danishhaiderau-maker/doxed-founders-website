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
