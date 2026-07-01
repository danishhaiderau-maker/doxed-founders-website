# Stack-wide health monitor — checks every surface that real-money reliability depends on.
# Run via scheduled task every 5 min. On any abnormality, writes logs/stack_health.json
# (with abnormality=true + details) and fires CRASH_NOTIFY_WEBHOOK so you get pinged,
# and a Cursor Automation (cron 5 min) can read logs/stack_health.json to auto-activate the AI.
param([switch]$Quiet)
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logsDir = Join-Path $repoRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }
$logFile = Join-Path $repoRoot ".stack-monitor.log"

function Mon-Log([string]$m) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
  Add-Content -Path $logFile -Value $line -EA SilentlyContinue
  if (-not $Quiet) { Write-Host $line }
}

function Probe([string]$url, [int]$timeoutSec = 12) {
  try {
    $code = curl.exe -s --max-time $timeoutSec -o NUL -w "%{http_code}" $url 2>$null
    return [int]$code
  } catch { return 0 }
}

$ts = (Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz")
$abnormalities = @()

# 1. Local stack (bot / analyzer / bridge / tunnel)
$botCode = Probe "http://127.0.0.1:7002/api/ping"
$anCode = Probe "http://127.0.0.1:9001/"
$bridgeCode = Probe "http://127.0.0.1:7810/health"
$tunnelCode = Probe "https://bot.doxxedcrypto.digital/api/ping" 15
if ($botCode -ne 200)    { $abnormalities += "bot_offline_ping=$botCode" }
if ($anCode -ne 200)     { $abnormalities += "analyzer_offline_ping=$anCode" }
if ($bridgeCode -ne 200) { $abnormalities += "bridge_offline_ping=$bridgeCode" }
if ($tunnelCode -ne 200) { $abnormalities += "tunnel_offline_ping=$tunnelCode" }

# 2. Railway API + Neon DB (via the API health endpoint)
$apiCode = Probe "https://doxxedcrypto.digital/api/health" 20
$dbStatus = "unknown"; $apiStatus = "unknown"
if ($apiCode -eq 200) {
  try {
    $body = curl.exe -s --max-time 20 "https://doxxedcrypto.digital/api/health" 2>$null | ConvertFrom-Json
    $apiStatus = $body.services.api
    $dbStatus = $body.services.database
    if ($dbStatus -ne "ok") { $abnormalities += "neon_db_not_ok=$dbStatus" }
    if ($apiStatus -ne "ok") { $abnormalities += "railway_api_not_ok=$apiStatus" }
  } catch { $abnormalities += "api_health_parse_failed" }
} else { $abnormalities += "railway_api_unreachable_ping=$apiCode" }

# 3. Public site (Vercel) — if the site is down, a deploy failed or Vercel is broken
$siteCode = Probe "https://doxxedcrypto.digital/" 20
if ($siteCode -eq 0 -or $siteCode -ge 500) { $abnormalities += "vercel_site_down_ping=$siteCode" }

# 4. GitHub — last commit age + CI status (if gh is authed)
$ghOk = $true; $ghNote = ""
try {
  $ghAuth = gh auth status 2>&1 | Out-String
  if ($ghAuth -match "Logged in") {
    $ci = gh run list --limit 1 --json status,conclusion,name 2>$null | ConvertFrom-Json
    if ($ci -and $ci[0].conclusion -eq "FAILURE") { $abnormalities += "github_ci_failed_run=$($ci[0].name)" }
    $ghNote = "ci=$($ci[0].status)/$($ci[0].conclusion)"
  } else { $ghOk = $false; $ghNote = "gh_not_authed" }
} catch { $ghOk = $false; $ghNote = "gh_error" }

# 5. Bot crash flag — if logs/last_crash.json was written in the last 10 min, flag it
$crashFile = Join-Path $logsDir "last_crash.json"
if (Test-Path $crashFile) {
  $ageMin = ((Get-Date) - (Get-Item $crashFile).LastWriteTime).TotalMinutes
  if ($ageMin -lt 10) { $abnormalities += "bot_crashed_${ageMin}min_ago" }
}

# 6. Relay sim orphan spike — read the sync score via the bridge if exposed (best-effort)
$relayNote = "skipped"

$report = [ordered]@{
  ts             = $ts
  abnormality    = ($abnormalities.Count -gt 0)
  abnormalities  = $abnormalities
  local          = [ordered]@{ bot=$botCode; analyzer=$anCode; bridge=$bridgeCode; tunnel=$tunnelCode }
  railway_api    = $apiCode
  api_status     = $apiStatus
  neon_db        = $dbStatus
  vercel_site    = $siteCode
  github         = $ghNote
  relay          = $relayNote
}
$json = $report | ConvertTo-Json -Depth 5
Set-Content -Path (Join-Path $logsDir "stack_health.json") -Value $json -Encoding UTF8
Add-Content -Path (Join-Path $logsDir "stack_health_history.jsonl") -Value ($json -replace "`n"," " -replace "`r","") -Encoding UTF8

if ($abnormalities.Count -gt 0) {
  Mon-Log ("ABNORMAL: " + ($abnormalities -join " | "))
  # Fire webhook so you get pinged on your phone/Discord
  $wh = (Get-Item -Path "env:CRASH_NOTIFY_WEBHOOK" -ErrorAction SilentlyContinue).Value
  if ($wh) {
    try {
      $body = @{ content = "STACK ABNORMALITY @ $ts`n$($abnormalities -join "`n")`nSee logs\stack_health.json" } | ConvertTo-Json
      Invoke-RestMethod -Uri $wh -Method Post -ContentType "application/json" -Body $body -TimeoutSec 5 -EA Stop | Out-Null
    } catch { }
  }
  try { msg * /TIME:30 "Stack abnormality: $($abnormalities -join ', '). See logs\stack_health.json" 2>$null } catch { }
} else {
  Mon-Log ("OK: bot=$botCode an=$anCode bridge=$bridgeCode tunnel=$tunnelCode api=$apiCode db=$dbStatus site=$siteCode gh=$ghNote")
}
