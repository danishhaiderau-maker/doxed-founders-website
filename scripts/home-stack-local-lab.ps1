# Retired external local-lab controller. It must never create a second AI loop.
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet("start", "stop", "status")]
  [string]$Action
)

$result = @{
  ok = ($Action -eq "status")
  retired = $true
  requestedAction = $Action
  message = "Local strategy lab is disabled; Fly.io is the sole AI/trading owner."
  canonicalRuntime = "https://doxed-btc-bot.fly.dev"
}
$result | ConvertTo-Json -Compress
if ($Action -ne "status") { exit 78 }
