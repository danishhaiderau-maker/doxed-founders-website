param(
  [string]$BotUrl = "https://doxed-btc-bot.fly.dev",
  [ValidateSet("pause", "resume")][string]$Action = "resume",
  [int]$TimeoutSec = 30
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
. (Join-Path $scriptDir "home-bot-vault-env.ps1")

$BotUrl = Get-CanonicalFlyBotUrl -RequestedUrl $BotUrl
$token = Import-CanonicalBotAdminToken
if (-not $token) {
  throw "BOT_ADMIN_TOKEN missing. Set vault home-bot.env (doxedcryptofounder-secrets\vault\home-bot.env)."
}

$headers = @{ "X-Bot-Admin-Token" = $token }
$base = $BotUrl.TrimEnd("/")

try {
  $result = Invoke-RestMethod -Uri "$base/api/$Action" -Method POST -Headers $headers -TimeoutSec $TimeoutSec
} catch {
  if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) {
    throw "Fly rejected admin token (401). Refresh vault home-bot.env to match Fly secrets."
  }
  throw
}

$status = Invoke-RestMethod -Uri "$base/api/status" -TimeoutSec $TimeoutSec
$wantPaused = $Action -eq "pause"
$confirmed = [bool]$status.execution_paused -eq $wantPaused

[pscustomobject]@{
  ok = $confirmed
  action = $Action
  bot_url = $base
  execution_paused = [bool]$status.execution_paused
  execution_reason = [string]$status.execution_reason
  bot_response = $result
  message = if ($confirmed) {
    if ($wantPaused) { "Trading paused and confirmed on Fly." } else { "Trading resumed and confirmed on Fly." }
  } else {
    "Fly answered $Action but /api/status did not confirm the expected pause state."
  }
}
